"""Phase one: build the ledger for an account.

Nothing is downloaded for its content here. The job is to establish, with
evidence, the list of things that existed: which repositories, which issues,
which pull requests, which commits, which profile pages. Each row records which
source produced it, because a row from the live API and a row inferred from an
archived URL do not carry the same weight.

The account may be live, suspended, or fully erased. The three cases are
distinguished by what answers, not by an assumption.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from . import events as events_lane
from .broker import Client
from .ledger import Ledger, LedgerEntry

# Path segments GitHub serves as site chrome rather than as a user's
# repository. A captured URL under the account path can name one of these.
RESERVED = {
    "achievements",
    "codespaces",
    "followers",
    "following",
    "issues",
    "login",
    "new",
    "organizations",
    "projects",
    "pulls",
    "search",
    "settings",
    "site",
    "sponsors",
    "stars",
    "topics",
}

# GitHub allows letters, digits, dot, hyphen and underscore, and a name may not
# start with punctuation. This rejects CSS fragments and query strings that a
# captured URL can contribute as a first path segment.
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def valid_repo_name(name: str) -> bool:
    """True for a name that could actually be a GitHub repository."""
    name = name.strip().removesuffix(".git")
    if not name or len(name) > 100:
        return False
    if name.lower() in RESERVED or name.lower() == "github.com":
        return False
    return bool(REPO_NAME.match(name))


def enumerate_account(
    client: Client,
    account: str,
    seeds: list[str] | None = None,
    workdir: Path | None = None,
    orgs: list[str] | None = None,
) -> Ledger:
    """Fill a ledger for `account` from every source that lists it.

    `seeds` are repository names a person already knows about. An archive only
    knows what it crawled, so a repository that was never captured and is now
    gone is invisible to every source here. A name supplied by a person is
    evidence too, and it is labelled as coming from a person rather than a
    crawl.
    """
    ledger = Ledger(account=account)

    _from_live_api(client, account, ledger)
    _from_events(client, account, ledger, workdir, orgs)
    _from_owner_cdx(client, account, ledger, workdir)
    _from_profile_captures(client, account, ledger)

    for name in seeds or []:
        name = name.removesuffix(".git")
        if valid_repo_name(name):
            ledger.add(
                LedgerEntry(
                    kind="repo",
                    owner=account,
                    repo=name,
                    url=f"https://github.com/{account}/{name}",
                    source="supplied by the operator",
                    note="no archive source listed this name",
                )
            )

    # Commit and issue search reach repositories none of the sources above can
    # see, because those sources enumerate the account and the archive, not the
    # account's activity. Run it here so its hits become recovery targets.
    enumerate_contributions(client, ledger)

    if not ledger.repos():
        ledger.gap(
            f"no repository name was found for {account} from any source; "
            "recovery has nothing to target"
        )
    return ledger


def _from_live_api(client: Client, account: str, ledger: Ledger) -> None:
    """If the account still answers, it is the best source there is."""
    user = client.github(f"users/{account}")
    if not isinstance(user, dict) or user.get("message") == "Not Found":
        ledger.gap(
            f"GET /users/{account} returned Not Found: the account is not "
            "readable through the API. This is consistent with a suspension, "
            "a rename, or a private-only account and does not distinguish them."
        )
        return

    ledger.account_id = user.get("id")
    ledger.add(
        LedgerEntry(
            kind="profile",
            owner=account,
            title=user.get("name") or account,
            created=user.get("created_at", ""),
            url=user.get("html_url", ""),
            source="live api /users",
            recovered=True,
        )
    )

    page = 1
    while page <= 10:
        repos = client.github(f"users/{account}/repos?per_page=100&page={page}")
        if not isinstance(repos, list) or not repos:
            break
        for repo in repos:
            ledger.add(
                LedgerEntry(
                    kind="repo",
                    owner=account,
                    repo=repo.get("name", ""),
                    title=repo.get("description") or "",
                    state="public" if not repo.get("private") else "private",
                    created=repo.get("created_at", ""),
                    url=repo.get("html_url", ""),
                    source="live api /users/repos",
                    note=f"default_branch={repo.get('default_branch', '')}",
                )
            )
        if len(repos) < 100:
            break
        page += 1


def _from_events(
    client: Client,
    account: str,
    ledger: Ledger,
    workdir: Path | None,
    orgs: list[str] | None = None,
) -> None:
    """Read the event feed, which outlives the profile.

    This runs before the archive sources because it is both cheaper and richer:
    one request returns up to a hundred events, each carrying the object it
    acted on, including the full body of an issue or a comment. When an account
    is suspended those objects are deleted from the repositories that held
    them, so this feed is often the only copy.
    """
    events, gaps = events_lane.fetch(client, account, orgs=orgs)
    for gap in gaps:
        ledger.gap(gap)
    if not events:
        return
    target = workdir if workdir is not None else Path(".")
    events_lane.save_raw(events, target)
    counts = events_lane.ingest(ledger, events, target, account)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    ledger.fact(f"event feed: {len(events)} events ({summary})")


def _from_owner_cdx(
    client: Client,
    account: str,
    ledger: Ledger,
    workdir: Path | None = None,
) -> None:
    """Every archived URL under the account path, which reveals repo names.

    This is the source that finds repositories a profile never displayed,
    including ones created after the last profile capture. An archived URL of
    the form /account/repo/something proves the repository `repo` existed.
    """
    save = (workdir if workdir is not None else Path(".")) / "archive" / "cdx-owner.json"
    rows = client.cdx(
        f"github.com/{account}/", lane="cdx-owner", limit=500, save_to=save
    )
    if rows is None:
        ledger.gap(
            "the owner-level Wayback CDX query returned no parseable array; "
            "archived repositories could not be enumerated from this source"
        )
        return

    base = f"https://github.com/{account}/"
    for row in rows:
        if len(row) < 5:
            continue
        timestamp, original, status = row[1], row[2], row[4]
        if not original.startswith(base):
            continue
        # Clipboard artefacts get archived as part of a URL: the string
        # "git https://github.com/..." shows up as a path segment.
        if " " in original or "%20" in original:
            continue
        name = unquote(original[len(base):].split("/")[0].split("?")[0])
        if not valid_repo_name(name):
            continue
        added = ledger.add(
            LedgerEntry(
                kind="repo",
                owner=account,
                repo=name,
                url=f"https://github.com/{account}/{name}",
                source=f"wayback cdx (status {status})",
                note=f"first seen {timestamp}",
            )
        )
        if added and status != "200":
            ledger.gap(
                f"{account}/{name} appears in CDX only with status {status} "
                f"at {timestamp}; its pages may never have been captured"
            )

    # Issues and pull requests captured directly are ledger rows too, since a
    # suspended account's trackers vanish along with it.
    for row in rows:
        if len(row) < 5 or row[4] != "200":
            continue
        original = row[2]
        match = re.search(r"/(?:pull|issues)/(\d+)$", original)
        if not match:
            continue
        kind = "pull" if "/pull/" in original else "issue"
        parts = original[len(base):].split("/")
        ledger.add(
            LedgerEntry(
                kind=kind,
                owner=account,
                repo=unquote(parts[0]),
                number=int(match.group(1)),
                url=original,
                source="wayback cdx",
                note=f"captured {row[1]}",
            )
        )


def _from_profile_captures(client: Client, account: str, ledger: Ledger) -> None:
    """Read an archived profile page for a repository list.

    The profile lists popular repositories, which is a second witness for a
    name that CDX may only hold under a deep path.
    """
    captures = client.captures(f"https://github.com/{account}", lane="archive-profile")
    if captures is None:
        ledger.gap("the archive did not answer for the profile URL")
        return

    for capture in captures:
        replay = capture.get("replay")
        if not replay:
            continue
        text = client.get_text(replay, lane="archive-profile")
        if not text:
            continue
        for name in re.findall(rf'href="/{re.escape(account)}/([\w.-]+)"', text):
            name = name.removesuffix(".git")
            if not valid_repo_name(name):
                continue
            ledger.add(
                LedgerEntry(
                    kind="repo",
                    owner=account,
                    repo=name,
                    url=f"https://github.com/{account}/{name}",
                    source="archived profile",
                    note=f"capture {capture.get('timestamp', '')}",
                )
            )


def enumerate_repo(client: Client, ledger: Ledger, owner: str, repo: str) -> None:
    """Fill in the pull requests, issues and commits of one repository.

    Tried against the live API first. When that 404s the repository is gone and
    this call records a gap naming what could not be listed, rather than
    silently returning nothing.
    """
    meta = client.github(f"repos/{owner}/{repo}")
    if isinstance(meta, dict) and meta.get("message"):
        if meta.get("message") == "Not Found":
            ledger.gap(
                f"GET /repos/{owner}/{repo} is not readable, so the pull request, "
                "issue and commit lists could not be enumerated from the live API"
            )
        else:
            ledger.gap(
                f"GET /repos/{owner}/{repo} was refused with "
                f"{meta.get('message')!r}; this is a request limit or an auth "
                "limit, not evidence about the repository"
            )
        return
    if isinstance(meta, dict) and meta.get("full_name"):
        ledger.add(
            LedgerEntry(
                kind="repo",
                owner=owner,
                repo=repo,
                title=meta.get("description") or "",
                created=meta.get("created_at", ""),
                url=meta.get("html_url", ""),
                source="live api /repos",
                recovered=True,
                note=f"default_branch={meta.get('default_branch', '')}",
            )
        )
        _commits(client, ledger, owner, repo)
        _pulls(client, ledger, owner, repo)
        _issues(client, ledger, owner, repo)
        return

    ledger.gap(
        f"GET /repos/{owner}/{repo} gave no JSON at all (status "
        f"{(meta or {}).get('_ghre_status')}), which is most likely the request "
        "budget being spent; the repository's pull requests, issues and "
        "commits are therefore unenumerated"
    )


def _commits(client: Client, ledger: Ledger, owner: str, repo: str) -> None:
    page = 1
    while page <= 10:
        rows = client.github(f"repos/{owner}/{repo}/commits?per_page=100&page={page}")
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            commit = row.get("commit", {})
            author = commit.get("author", {}) or {}
            ledger.add(
                LedgerEntry(
                    kind="commit",
                    owner=owner,
                    repo=repo,
                    sha=row.get("sha", ""),
                    title=(commit.get("message") or "").splitlines()[0][:200],
                    created=author.get("date", ""),
                    url=row.get("html_url", ""),
                    source="live api /commits",
                )
            )
        if len(rows) < 100:
            break
        page += 1


def _pulls(client: Client, ledger: Ledger, owner: str, repo: str) -> None:
    rows = client.github(f"repos/{owner}/{repo}/pulls?state=all&per_page=100")
    if not isinstance(rows, list):
        return
    for row in rows:
        ledger.add(
            LedgerEntry(
                kind="pull",
                owner=owner,
                repo=repo,
                number=row.get("number"),
                title=row.get("title", ""),
                state=row.get("state", ""),
                created=row.get("created_at", ""),
                sha=(row.get("head") or {}).get("sha", ""),
                url=row.get("html_url", ""),
                source="live api /pulls",
            )
        )


def _issues(client: Client, ledger: Ledger, owner: str, repo: str) -> None:
    rows = client.github(f"repos/{owner}/{repo}/issues?state=all&per_page=100")
    if not isinstance(rows, list):
        return
    for row in rows:
        if row.get("pull_request"):
            continue
        ledger.add(
            LedgerEntry(
                kind="issue",
                owner=owner,
                repo=repo,
                number=row.get("number"),
                title=row.get("title", ""),
                state=row.get("state", ""),
                created=row.get("created_at", ""),
                url=row.get("html_url", ""),
                source="live api /issues",
            )
        )


def enumerate_contributions(client: Client, ledger: Ledger) -> None:
    """Find commits and issues the account authored in other repositories.

    A suspended account's own work is gone, but a commit it wrote inside
    somebody else's repository stays in that repository and stays searchable.
    Each hit is a ledger row pointing at a repository that still exists, which
    is the most recoverable material available.
    """
    for kind, suffix, path in (
        ("commit", "", "search/commits"),
        ("issue", "is:issue", "search/issues"),
    ):
        q = f"author:{ledger.account} {suffix}".strip()
        data = client.github(f"{path}?q={_quote(q)}&per_page=50")
        if not isinstance(data, dict) or "_ghre_status" in data:
            ledger.gap(
                f"the {kind} search returned no JSON (status "
                f"{(data or {}).get('_ghre_status')}), so contributions "
                "elsewhere are unenumerated"
            )
            continue
        if data.get("message"):
            # GitHub answers 422 rather than an empty set when the account a
            # qualifier names does not exist. That is a fact about the account,
            # not a tool failure.
            ledger.gap(
                f"the {kind} search rejected author:{ledger.account}: "
                f"{data.get('message')}. An unknown account in an author "
                "qualifier is refused outright, so this source is unusable for "
                "this account and does not count as a negative result."
            )
            continue
        if data.get("incomplete_results"):
            ledger.gap(
                f"the {kind} search reported incomplete_results=true; its count "
                "is a floor, not a total"
            )
        for item in data.get("items", []) or []:
            # Commit search returns `repository.full_name`; issue search
            # returns `repository_url`. Reading only one silently loses the
            # other half.
            repo_url = item.get("repository_url", "")
            owner = repo = ""
            if repo_url:
                parts = repo_url.rstrip("/").split("/")
                owner = parts[-2] if len(parts) >= 2 else ""
                repo = parts[-1] if parts else ""
            else:
                full = (item.get("repository") or {}).get("full_name", "")
                owner, _, repo = full.partition("/")
            if not (owner and repo):
                continue
            ledger.add(
                LedgerEntry(
                    kind="repo",
                    owner=owner,
                    repo=repo,
                    url=f"https://github.com/{owner}/{repo}",
                    source=f"search {kind}s (a contribution lives here)",
                    recovered=True,
                )
            )
            commit = item.get("commit") or {}
            ledger.add(
                LedgerEntry(
                    kind=kind,
                    owner=owner,
                    repo=repo,
                    number=item.get("number"),
                    sha=item.get("sha", ""),
                    title=(
                        (commit.get("message") or "").splitlines()[0]
                        if commit
                        else (item.get("title") or "")
                    )[:200],
                    state=item.get("state", ""),
                    created=item.get("created_at", "")
                    or ((commit.get("author") or {}).get("date", "")),
                    url=item.get("html_url", ""),
                    source=f"search {kind}s",
                )
            )


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
