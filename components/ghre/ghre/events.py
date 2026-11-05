"""The events lane: what the account did, as GitHub recorded it.

GitHub publishes a per-account event feed, and that feed answers for an
account whose profile returns 404. It is the only surviving witness for
actions whose objects went with the account: an issue the account opened is
removed from the repository's issue list, so `/repos/OWNER/REPO/issues/N`
answers 404 even though the repository itself is intact and readable.

Each payload carries the object as it existed when the event fired. An
`IssuesEvent` carries the whole issue, body included. An `IssueCommentEvent`
carries the whole comment. That makes this lane a recovery source rather than
only an index, and it is why it runs before any request that costs more.

The feed covers roughly the last 90 days and is capped by GitHub, so it is not
a complete history. What it does hold, it holds in full.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .broker import Client
from .ledger import Ledger, LedgerEntry

# The public feed is what survives a suspension; the authenticated one is
# refused, and the org feed only exists for organisations the account belongs
# to, so both are tried and either may answer nothing.
ENDPOINTS = (
    ("users/{account}/events/public", "events-public"),
    ("users/{account}/received_events/public", "events-received"),
    ("users/{account}/events/orgs/{org}", "events-org"),
)

# Event types that name a repository. A row here is proof the account touched
# that repository, which makes it a recovery target even when nothing else
# recorded the relationship.
REPO_EVENTS = {
    "ForkEvent",
    "WatchEvent",
    "PublicEvent",
    "MemberEvent",
    "ReleaseEvent",
}


def fetch(
    client: Client,
    account: str,
    pages: int = 5,
    per_page: int = 100,
    orgs: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the account's event feeds. Returns (events, gaps).

    Events are deduplicated by id, because the same event appears in more than
    one feed and in more than one page boundary. A feed that refuses is a gap,
    not a negative: the account may still have events the feed did not return.
    """
    events: list[dict[str, Any]] = []
    gaps: list[str] = []
    seen: set[str] = set()

    for template, label in ENDPOINTS:
        targets = [account]
        if "{org}" in template:
            targets = list(orgs or [])
            if not targets:
                gaps.append(
                    "no organisation was named, so the org event feed was not asked"
                )
        for target in targets:
            path = template.format(account=account, org=target)
            for page in range(1, pages + 1):
                url = f"{path}?per_page={per_page}&page={page}"
                data = client.github(url, lane=label)
                if isinstance(data, dict) and data.get("message"):
                    gaps.append(
                        f"the {label} feed refused page {page} with "
                        f"{data.get('message')!r}; events beyond that page are "
                        "unlisted"
                    )
                    break
                if not isinstance(data, list):
                    gaps.append(
                        f"the {label} feed gave no JSON for page {page}; events "
                        "beyond that page are unlisted"
                    )
                    break
                if not data:
                    break
                for event in data:
                    key = str(event.get("id", ""))
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    events.append(event)
                if len(data) < per_page:
                    break

    if not events:
        gaps.append(
            f"the event feed returned no events for {account}. The feed covers "
            "roughly 90 days, so this means either no public activity in that "
            "window or an account that never had any; it does not mean the "
            "account had no history."
        )
    return events, gaps


def ingest(
    ledger: Ledger,
    events: list[dict[str, Any]],
    workdir: Path,
    account: str,
) -> dict[str, int]:
    """Record every event in the ledger and save the bodies they carry.

    Returns counts per kind, so a caller can report what the lane produced
    instead of claiming it ran.
    """
    out = workdir / "events"
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    written: set[Path] = set()

    for event in events:
        kind = event.get("type", "")
        repo_full = (event.get("repo") or {}).get("name", "")
        owner, _, repo = repo_full.partition("/")
        payload = event.get("payload") or {}
        when = event.get("created_at", "")
        counts[kind] = counts.get(kind, 0) + 1

        if repo_full:
            ledger.add(
                LedgerEntry(
                    kind="repo",
                    owner=owner,
                    repo=repo,
                    url=f"https://github.com/{repo_full}",
                    source=f"events api ({kind})",
                    recovered=True,
                    note=f"touched {when}",
                )
            )

        if kind == "IssuesEvent":
            _take_issue(ledger, out, payload.get("issue") or {}, repo_full,
                        when, account, written)
        elif kind == "IssueCommentEvent":
            _take_issue(ledger, out, payload.get("issue") or {}, repo_full,
                        when, account, written)
            _take_comment(ledger, out, payload.get("comment") or {}, repo_full,
                          when, account, written)
        elif kind == "PullRequestEvent":
            _take_pull(ledger, payload.get("pull_request") or {}, repo_full, when)
        elif kind == "PushEvent":
            _take_push(ledger, payload, repo_full, when)
        elif kind == "CreateEvent":
            ledger.add(
                LedgerEntry(
                    kind="ref",
                    owner=owner,
                    repo=repo,
                    sha=(payload.get("sha") or "")[:40],
                    title=f"{payload.get('ref_type', 'ref')} {payload.get('ref', '')}".strip(),
                    created=when,
                    url=f"https://github.com/{repo_full}",
                    source="events api (CreateEvent)",
                )
            )
        elif kind in REPO_EVENTS:
            ledger.add(
                LedgerEntry(
                    kind="activity",
                    owner=owner,
                    repo=repo,
                    title=kind,
                    created=when,
                    url=f"https://github.com/{repo_full}",
                    source="events api",
                )
            )

    return counts


def _take_issue(
    ledger: Ledger,
    out: Path,
    issue: dict[str, Any],
    repo_full: str,
    when: str,
    account: str,
    written: set[Path],
) -> None:
    """Record an issue and write its body if the payload carried one."""
    number = issue.get("number")
    if not number:
        return
    owner, _, repo = repo_full.partition("/")
    author = (issue.get("user") or {}).get("login", "")
    entry = LedgerEntry(
        kind="issue",
        owner=owner,
        repo=repo,
        number=number,
        title=(issue.get("title") or "")[:200],
        state=issue.get("state", ""),
        created=issue.get("created_at") or when,
        url=issue.get("html_url", ""),
        source="events api (issue payload)",
        note=f"author={author}" if author else "",
    )

    body = issue.get("body") or ""
    if body:
        path = out / f"{owner}__{repo}" / f"issue-{number}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path not in written:
            path.write_text(_render(issue, body, author, when), encoding="utf-8")
            written.add(path)
            entry.recovered = True
            entry.local_path = str(path.relative_to(out.parent))
    ledger.add(entry)


def _take_comment(
    ledger: Ledger,
    out: Path,
    comment: dict[str, Any],
    repo_full: str,
    when: str,
    account: str,
    written: set[Path],
) -> None:
    """Record a comment and write its body if the payload carried one."""
    comment_id = comment.get("id")
    if not comment_id:
        return
    owner, _, repo = repo_full.partition("/")
    issue_number = comment.get("issue_url", "").rsplit("/", 1)[-1]
    author = (comment.get("user") or {}).get("login", "")
    body = comment.get("body") or ""

    entry = LedgerEntry(
        kind="comment",
        owner=owner,
        repo=repo,
        number=int(issue_number) if issue_number.isdigit() else None,
        created=comment.get("created_at") or when,
        url=comment.get("html_url", ""),
        source="events api (comment payload)",
        note=f"comment id {comment_id}"
        + (f", author={author}" if author else ""),
    )
    if body:
        path = out / f"{owner}__{repo}" / f"comment-{comment_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path not in written:
            path.write_text(
                _render(comment, body, author, when, kind="comment"),
                encoding="utf-8",
            )
            written.add(path)
            entry.recovered = True
            entry.local_path = str(path.relative_to(out.parent))
    ledger.add(entry)


def _take_pull(
    ledger: Ledger,
    pull: dict[str, Any],
    repo_full: str,
    when: str,
) -> None:
    number = pull.get("number")
    if not number:
        return
    owner, _, repo = repo_full.partition("/")
    ledger.add(
        LedgerEntry(
            kind="pull",
            owner=owner,
            repo=repo,
            number=number,
            title=(pull.get("title") or "")[:200],
            state=pull.get("state", ""),
            sha=((pull.get("head") or {}).get("sha") or "")[:40],
            created=pull.get("created_at") or when,
            url=pull.get("html_url", ""),
            source="events api (pull_request payload)",
        )
    )


def _take_push(
    ledger: Ledger,
    payload: dict[str, Any],
    repo_full: str,
    when: str,
) -> None:
    """A push names commit SHAs, which are recovery targets for the git lane."""
    owner, _, repo = repo_full.partition("/")
    for commit in payload.get("commits") or []:
        sha = commit.get("sha", "")
        if not sha:
            continue
        ledger.add(
            LedgerEntry(
                kind="commit",
                owner=owner,
                repo=repo,
                sha=sha,
                title=(commit.get("message") or "").splitlines()[0][:200],
                created=when,
                url=f"https://github.com/{repo_full}/commit/{sha}",
                source="events api (push payload)",
            )
        )


def _render(
    item: dict[str, Any],
    body: str,
    author: str,
    when: str,
    kind: str = "issue",
) -> str:
    """Write a body with the provenance a reader needs to trust it."""
    number = item.get("number") or item.get("id") or ""
    title = item.get("title") or ""
    url = item.get("html_url") or ""
    lines = [
        f"# {kind} {number}: {title}".rstrip(": "),
        "",
        f"- author: {author or 'unknown'}",
        f"- event time: {when}",
        f"- url: {url or 'unknown'}",
        "- source: GitHub events API payload, which carries the object as it",
        "  existed when the event fired. The object itself may since have been",
        "  deleted; this text is what survives.",
        "",
        "---",
        "",
        body,
        "",
    ]
    return "\n".join(lines)


def save_raw(events: list[dict[str, Any]], workdir: Path) -> Path:
    """Keep the unfiltered feed, so a later reader can re-derive anything."""
    path = workdir / "events" / "events.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    return path
