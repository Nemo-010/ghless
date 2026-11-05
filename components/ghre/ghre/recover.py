"""Phase two: recover bodies for what the ledger recorded.

Recovery is ordered by expected yield, not by ledger order. A commit object
inside a live repository is worth more than an archived page, and a page is
worth more than a search snippet. Each function returns the number of things it
recovered and records everything it could not get as a gap.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .broker import Client
from .ledger import Ledger, LedgerEntry, classify_path

# git is how pull-request refs and branch commits are read, because no REST
# endpoint serves them for a deleted account and the refs are the best source
# there is. Under the default transport the clone URL points at a broker that
# forwards the git smart-HTTP protocol, so a host with no direct route to
# GitHub can still do this work.
#
# The parent environment is preserved. git needs PATH, HOME and any egress
# proxy already configured on the host; replacing the environment instead of
# extending it makes git fail with "Could not resolve host".
GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/true",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(args: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=GIT_ENV,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, str(exc)


def recover_refs(
    ledger: Ledger,
    workdir: Path,
    account: str,
    max_repos: int = 40,
    extra_repos: list[str] | None = None,
    git_base: str = "https://github.com",
) -> int:
    """Fetch every pull-request ref from live repositories and keep the hits.

    GitHub answers 404 for a suspended account's pull requests, but the
    refs/pull/N/head objects stay in the destination repository. One
    `git fetch +refs/pull/*/head:refs/pr/*` per repository brings them all in,
    and the author line names the account even when its profile is gone.

    Repositories owned by the account are skipped: they are the ones that
    vanish, so cloning them cannot work. The targets are the other people's
    repositories the ledger proved the account touched.

    A pull-ref-only sweep reads nothing from a repository that has no pull
    refs, so plain branches are swept too. That is not a refinement; on one
    run it found a third of the commits.
    """
    recovered = 0
    targets: list[tuple[str, str]] = []
    for entry in ledger.entries:
        if entry.repo and entry.owner.lower() != account.lower():
            pair = (entry.owner, entry.repo)
            if pair not in targets:
                targets.append(pair)
    for item in extra_repos or []:
        owner, _, name = item.partition("/")
        if owner and name and (owner, name) not in targets:
            targets.append((owner, name))

    refs_root = workdir / "refs"
    refs_root.mkdir(parents=True, exist_ok=True)
    seen_shas: set[str] = set()

    for owner, repo in targets[:max_repos]:
        slug = f"{owner}__{repo}".replace("/", "_")
        clone = refs_root / slug
        if not clone.exists():
            code, out = _git(
                [
                    "clone",
                    "--bare",
                    "--filter=blob:none",
                    f"{git_base}/{owner}/{repo}",
                    str(clone),
                ],
                workdir,
                timeout=180,
            )
            if code != 0:
                if "not found" in out.lower():
                    ledger.gap(
                        f"github.com/{owner}/{repo} refused the clone as not "
                        "found, so its refs are unavailable over git"
                    )
                continue
        _git(["fetch", "origin", "+refs/pull/*/head:refs/pr/*"], clone, timeout=180)
        _git(["fetch", "origin", "+refs/heads/*:refs/heads/*"], clone, timeout=180)

        code, log = _git(
            ["log", "--all", "--format=%H%x09%an <%ae>%x09%aI%x09%s"],
            clone,
            timeout=120,
        )
        if code != 0:
            continue
        for line in log.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            sha, author, date, subject = parts[0], parts[1], parts[2], parts[3]
            if account.lower() not in author.lower():
                continue
            seen_shas.add(sha)
            entry = _find_commit(ledger, sha) or LedgerEntry(
                kind="commit",
                owner=owner,
                repo=repo,
                sha=sha,
                title=subject[:200],
                created=date,
                source=f"git refs in {owner}/{repo}",
            )
            entry.recovered = True
            ledger.add(entry)
            patch_dir = refs_root / slug / "patches"
            patch_dir.mkdir(parents=True, exist_ok=True)
            patch = patch_dir / f"{sha[:12]}.patch"
            if patch.exists():
                continue
            code, diff = _git(["show", "--format=fuller", sha], clone, timeout=120)
            if code == 0 and diff:
                patch.write_text(diff, encoding="utf-8")
                entry.local_path = str(patch.relative_to(workdir))
                recovered += 1
    return recovered


def _find_commit(ledger: Ledger, sha: str) -> LedgerEntry | None:
    for entry in ledger.entries:
        if entry.sha and entry.sha.lower() == sha.lower():
            return entry
    return None


def recover_object(
    ledger: Ledger,
    workdir: Path,
    account: str,
    shas: list[str],
    max_repos: int = 40,
    extra_repos: list[str] | None = None,
    git_base: str = "https://github.com",
) -> int:
    """Try to fetch a known commit object by SHA from every candidate repo.

    A clone whose object store still reaches the object serves it even when the
    originating repository is gone, because the object is reachable from a ref
    the live repository still has.
    """
    recovered = 0
    targets: list[tuple[str, str]] = []
    for entry in ledger.entries:
        if entry.repo and entry.owner.lower() != account.lower():
            pair = (entry.owner, entry.repo)
            if pair not in targets:
                targets.append(pair)
    for item in extra_repos or []:
        owner, _, name = item.partition("/")
        if owner and name and (owner, name) not in targets:
            targets.append((owner, name))

    out_root = workdir / "objects"
    out_root.mkdir(parents=True, exist_ok=True)
    for owner, repo in targets[:max_repos]:
        slug = f"{owner}__{repo}".replace("/", "_")
        clone = workdir / "refs" / slug
        if not clone.exists():
            continue
        for sha in shas:
            code, _ = _git(["cat-file", "-e", f"{sha}^{{commit}}"], clone, timeout=30)
            if code != 0:
                _git(["fetch", "origin", sha], clone, timeout=90)
                code, _ = _git(["cat-file", "-e", f"{sha}^{{commit}}"], clone, timeout=30)
                if code != 0:
                    continue
            code, diff = _git(["show", "--format=fuller", sha], clone, timeout=120)
            if code != 0 or not diff:
                continue
            patch = out_root / f"{sha[:12]}.patch"
            patch.write_text(diff, encoding="utf-8")
            ledger.add(
                LedgerEntry(
                    kind="commit",
                    owner=owner,
                    repo=repo,
                    sha=sha,
                    source=f"fetched by sha from {owner}/{repo}",
                    recovered=True,
                    local_path=str(patch.relative_to(workdir)),
                )
            )
            recovered += 1
    return recovered


def recover_archive(
    client: Client,
    ledger: Ledger,
    workdir: Path,
    account: str,
    max_pages: int = 40,
) -> int:
    """Fetch archived bodies for every repository the ledger knows about.

    CDX is queried per repository rather than per guessed URL, so the capture
    list is whatever was actually archived instead of what was expected to
    exist. Bodies are written with their timestamp so two captures of the same
    page stay distinguishable.
    """
    out = workdir / "archive"
    out.mkdir(parents=True, exist_ok=True)
    recovered = 0
    pages = 0

    for repo in ledger.repos()[:20]:
        owner, _, name = repo.partition("/")
        rows = client.cdx(f"github.com/{owner}/{name}", lane="cdx-repo", limit=200)
        if rows is None:
            ledger.gap(f"CDX returned nothing parseable for {repo}")
            continue
        for row in rows:
            if pages >= max_pages:
                break
            if len(row) < 5 or row[4] != "200":
                continue
            timestamp, original = row[1], row[2]
            if classify_path(original) not in {"file", "commit", "thread", "commits"}:
                continue
            pages += 1
            recovered += _recover_one_page(client, ledger, out, timestamp, original)
    return recovered


def _recover_one_page(
    client: Client,
    ledger: Ledger,
    out: Path,
    timestamp: str,
    original: str,
) -> int:
    """Fetch one archived page and, if it is a commit, its whole diff."""
    html = client.replay(timestamp, original)
    name = re.sub(r"[^\w.-]+", "_", original.split("://", 1)[-1])[:120]
    if not html:
        ledger.gap(f"no body for {original} at {timestamp}")
        return 0

    (out / f"{name}.html").write_text(html, encoding="utf-8")
    count = 1
    if "/commit/" in original:
        count += _extract_commit_diff(html, ledger, out, name)
    return count


def _extract_commit_diff(html: str, ledger: Ledger, out: Path, name: str) -> int:
    """Reconstruct files from the JSON a GitHub commit page embeds.

    The page carries `<script data-target="react-app.embeddedData">` holding
    every rendered diff entry. Entries flagged isTooBig are not in the page at
    all, so they are recorded as gaps rather than written as empty files.
    """
    match = re.search(
        r'<script type="application/json" data-target="react-app\.embeddedData">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        return 0
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return 0

    entries: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if "diffLines" in node and "path" in node:
                entries.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    written = 0
    files_root = out / f"{name}.files"
    for entry in entries:
        path = entry.get("path", "")
        if not path:
            continue
        if entry.get("isTooBig") or not entry.get("diffLines"):
            ledger.gap(
                f"{path} in {name} is not rendered in the captured page "
                f"({entry.get('lineCount')} lines); only its name and size survive"
            )
            continue
        lines = []
        for diff in entry["diffLines"]:
            text = diff.get("text", "")
            if text.startswith("+"):
                text = text[1:]
            lines.append(text)
        target = files_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written += 1
    return written


def recover_readable(
    client: Client, ledger: Ledger, workdir: Path, max_pages: int = 30
) -> int:
    """Save a captured issue or pull request as a document.

    The raw archived HTML is kept for reconstruction; this pass writes the same
    page as text so a reader does not have to open 200 KB of markup.
    """
    out = workdir / "readable"
    out.mkdir(parents=True, exist_ok=True)
    recovered = 0

    for entry in ledger.entries:
        if recovered >= max_pages:
            break
        if entry.kind not in {"issue", "pull"} or not entry.url:
            continue
        captures = client.captures(entry.url, lane="archive-thread")
        if not captures:
            ledger.gap(f"no capture for {entry.url}")
            continue
        stamp = captures[-1].get("timestamp", "")
        if not stamp:
            continue
        text = client.replay(stamp, entry.url, lane="archive-thread")
        if not text:
            ledger.gap(f"no readable body for {entry.url} at {stamp}")
            continue
        name = re.sub(r"[^\w.-]+", "_", entry.url.split("://", 1)[-1])[:120]
        (out / f"{name}.md").write_text(text, encoding="utf-8")
        entry.recovered = True
        entry.local_path = f"readable/{name}.md"
        recovered += 1
    return recovered


def mine_snippets(
    client: Client,
    ledger: Ledger,
    workdir: Path,
    account: str,
    terms: list[str] | None = None,
    max_queries: int = 40,
) -> int:
    """Record what the GitHub search API still returns for this account.

    A deleted page's prose sometimes survives in an index that is still
    queryable. GitHub's own issue search is the one index this tool can reach
    without a third party, and it answers with a title and the first part of a
    body. Each result is kept with the query that produced it, so a reader can
    see how it was found.
    """
    out = workdir / "snippets"
    out.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    spent = 0

    queries = [f'"{account}"']
    for repo in ledger.repos():
        name = repo.split("/", 1)[-1]
        queries.append(f'"{name}"')
        queries += [f'"{name}" {term}' for term in (terms or [])]

    for query in queries:
        for path, kind in (
            ("search/issues", "issue"),
            ("search/commits", "commit"),
            ("search/repositories", "repo"),
        ):
            if spent >= max_queries:
                break
            spent += 1
            data = client.github(
                f"{path}?q={_quote(query)}&per_page=20", lane="search"
            )
            if not isinstance(data, dict) or "_ghre_status" in data:
                continue
            if data.get("message"):
                continue
            for item in data.get("items", []) or []:
                url = item.get("html_url", "")
                text = (item.get("body") or item.get("title") or "").strip()
                if not url or not text:
                    continue
                key = f"{url}\t{text[:120]}"
                if key in seen:
                    continue
                seen[key] = f"{query} :: {kind}"
        if spent >= max_queries:
            break

    lines = ["# Snippets", ""]
    for key, origin in sorted(seen.items(), key=lambda kv: len(kv[0]), reverse=True):
        url, text = key.split("\t", 1)
        lines.append(f"- {url}")
        lines.append(f"  - {text}")
        lines.append(f"  - source: {origin}")
    (out / "snippets.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(seen)


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
