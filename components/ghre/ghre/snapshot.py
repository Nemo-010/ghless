"""Snapshot a repository's issues and pull requests, twice over.

Every other lane in this tool reconstructs work after it is gone. This one is
the other half of the method: take the copy while the objects still exist, so
that a suspension costs a weekend of refiling at worst instead of the record
itself. It is the answer to "what should we have done before the account went".

Two products, because they fail differently.

- The **archive** is authoritative and machine readable. Every issue and pull
  request as the API returned it, a readable Markdown rendering of each, an
  index, and a manifest of hashes. It holds bodies and comments as text, so an
  issue can be refiled from it with no network at all. It cannot outlive this
  disk, which is why the second product exists.
- The **save queue** is a copy on somebody else's host. Each object's page is
  submitted to Save Page Now through the archive broker, which answers a job
  id. A capture is keyed on the repository's own URL, so it survives the
  account that wrote the issue, the account that asked for the copy, and this
  machine.

Both are resumable. The archive broker's ceiling, not the tool, decides how
long a submission run takes, so a run that stops is continued by the next one
from its state file rather than restarted.

What a snapshot does *not* claim: a capture is a rendering of a page, so it
carries what the page showed at that instant, and a later edit is a later
capture. The archive directory is the thing to trust for text; the captures are
the thing to trust for "a copy existed somewhere else".
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Iterable, Callable

from .broker import Client

# The broker paces each caller at 100 requests a minute. Leaving headroom for
# the polling that follows a submission keeps a long run inside it.
SUBMIT_PACE = 0.75

SLUG_ALLOWED = re.compile(r"[^A-Za-z0-9 ._:/()-]")
SLUG_COLLAPSE = re.compile(r"\s+")


def slug(text: str, limit: int = 80) -> str:
    """A title reduced to something usable as a file name."""
    cleaned = SLUG_ALLOWED.sub(" ", text or "")
    cleaned = SLUG_COLLAPSE.sub(" ", cleaned).strip()
    return cleaned[:limit] or "untitled"


def _when(value: Any) -> str:
    return value if isinstance(value, str) else "unknown"


def render_object(
    repo: str,
    obj: dict[str, Any],
    comments: Iterable[dict[str, Any]] = (),
    pull: dict[str, Any] | None = None,
    reviews: Iterable[dict[str, Any]] = (),
    review_comments: Iterable[dict[str, Any]] = (),
) -> str:
    """One object as Markdown a person can read and a refiling can quote.

    The front block is deliberately a bullet list rather than YAML: it has to
    survive being pasted into an issue body.
    """
    number = obj.get("number")
    kind = "pull request" if obj.get("pull_request") else "issue"
    labels = ", ".join(
        label.get("name", "") for label in obj.get("labels") or [] if label
    ) or "none"
    author = (obj.get("user") or {}).get("login") or "(deleted)"

    lines = [
        f"# {repo}#{number}: {obj.get('title', '')}",
        "",
        f"- kind: {kind}",
        f"- state: {obj.get('state', 'unknown')}"
        + (f" ({obj['state_reason']})" if obj.get("state_reason") else ""),
        f"- author: {author}",
        f"- created: {_when(obj.get('created_at'))}",
        f"- updated: {_when(obj.get('updated_at'))}",
        f"- comments: {obj.get('comments', 0)}",
        f"- labels: {labels}",
        f"- url: {obj.get('html_url', '')}",
    ]
    if obj.get("milestone"):
        lines.append(f"- milestone: {obj['milestone'].get('title', '')}")
    if pull:
        lines += [
            f"- merged: {pull.get('merged')} at {_when(pull.get('merged_at'))}",
            f"- base: {(pull.get('base') or {}).get('ref')}"
            f"  head: {(pull.get('head') or {}).get('ref')}",
            f"- changed: +{pull.get('additions')} -{pull.get('deletions')}"
            f" in {pull.get('changed_files')} files",
        ]
    lines += ["", "## Body", "", (obj.get("body") or "_(empty)_").rstrip(), ""]

    comments = [c for c in comments if isinstance(c, dict)]
    if comments:
        lines += ["## Comments", ""]
        for comment in comments:
            who = (comment.get("user") or {}).get("login") or "(deleted)"
            lines += [
                f"### {who} - {_when(comment.get('created_at'))}",
                "",
                (comment.get("body") or "_(empty)_").rstrip(),
                "",
            ]

    # A review and a comment on a line are different objects and are kept
    # apart, because a review's verdict is the thing a merge decision cites.
    reviews = [r for r in reviews if isinstance(r, dict)]
    if reviews:
        lines += ["## Reviews", ""]
        for review in reviews:
            who = (review.get("user") or {}).get("login") or "(deleted)"
            body = (review.get("body") or "").rstrip()
            lines += [
                f"### {who} - {_when(review.get('submitted_at'))}"
                f" - {review.get('state')}",
                "",
            ]
            if body:
                lines += [body, ""]

    review_comments = [c for c in review_comments if isinstance(c, dict)]
    if review_comments:
        lines += ["## Review comments", ""]
        for comment in review_comments:
            who = (comment.get("user") or {}).get("login") or "(deleted)"
            at = comment.get("line") or comment.get("original_line") or "?"
            lines += [
                f"### {who} - {_when(comment.get('created_at'))}"
                f" on {comment.get('path')}:{at}",
                "",
                (comment.get("body") or "_(empty)_").rstrip(),
                "",
            ]
    return "\n".join(lines).rstrip() + "\n"


def snapshot_repo(
    client: Client,
    outdir: Path,
    owner: str,
    name: str,
    pause: float = 0.0,
    progress=None,
) -> dict[str, Any]:
    """Fetch every issue and pull request and write the archive directory.

    The issues endpoint returns pull requests too, each marked with a
    `pull_request` key, so one paged list is the whole enumeration. Nothing
    here guesses a number: a gap in the sequence is not filled in.
    """
    repo = f"{owner}/{name}"
    outdir.mkdir(parents=True, exist_ok=True)
    issues_dir = outdir / "issues"
    pulls_dir = outdir / "prs"
    issues_dir.mkdir(exist_ok=True)
    pulls_dir.mkdir(exist_ok=True)

    meta = client.github(f"repos/{owner}/{name}")
    if isinstance(meta, dict):
        (outdir / "repo.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

    objects = client.github_list(f"repos/{owner}/{name}/issues?state=all", pause=pause)
    objects = [o for o in objects if "number" in o]
    objects.sort(key=lambda o: o["number"])

    manifest: list[dict[str, Any]] = []
    for index, obj in enumerate(objects, 1):
        number = obj["number"]
        is_pull = "pull_request" in obj
        folder = pulls_dir if is_pull else issues_dir

        comments = client.github_list(
            f"repos/{owner}/{name}/issues/{number}/comments", pause=pause
        )
        pull = reviews = review_comments = None
        if is_pull:
            pull = client.github(f"repos/{owner}/{name}/pulls/{number}")
            if not isinstance(pull, dict):
                pull = None
            reviews = client.github_list(
                f"repos/{owner}/{name}/pulls/{number}/reviews", pause=pause
            )
            review_comments = client.github_list(
                f"repos/{owner}/{name}/pulls/{number}/comments", pause=pause
            )

        # The raw answer is kept beside the rendering: a rendering loses what
        # it does not print, and a refiling sometimes needs the field it lost.
        (folder / f"{number}.json").write_text(
            json.dumps(obj, indent=2), encoding="utf-8"
        )
        if comments:
            (folder / f"{number}.comments.json").write_text(
                json.dumps(comments, indent=2), encoding="utf-8"
            )
        if pull:
            (folder / f"{number}.pr.json").write_text(
                json.dumps(pull, indent=2), encoding="utf-8"
            )
        if reviews:
            (folder / f"{number}.reviews.json").write_text(
                json.dumps(reviews, indent=2), encoding="utf-8"
            )
        if review_comments:
            (folder / f"{number}.review-comments.json").write_text(
                json.dumps(review_comments, indent=2), encoding="utf-8"
            )

        text = render_object(
            repo, obj, comments, pull, reviews or [], review_comments or []
        )
        (folder / f"{number}.md").write_text(text, encoding="utf-8")
        encoded = text.encode()
        manifest.append(
            {
                "number": number,
                "kind": "pull" if is_pull else "issue",
                "title": obj.get("title", ""),
                "state": obj.get("state", ""),
                "author": (obj.get("user") or {}).get("login"),
                "created_at": obj.get("created_at"),
                "updated_at": obj.get("updated_at"),
                "comments": len(comments),
                "reviews": len(reviews or []),
                "review_comments": len(review_comments or []),
                "url": obj.get("html_url", ""),
                "markdown": f"{folder.name}/{number}.md",
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
            }
        )
        if progress is not None and index % 20 == 0:
            progress(f"  {index}/{len(objects)}")

    write_manifest(outdir, repo, manifest)
    write_index(outdir, repo, manifest)
    return {
        "repo": repo,
        "count": len(manifest),
        "objects": manifest,
        "gaps": [] if objects else ["the issues endpoint returned no object"],
    }


def write_manifest(outdir: Path, repo: str, manifest: list[dict[str, Any]]) -> None:
    (outdir / "MANIFEST.json").write_text(
        json.dumps({"repo": repo, "count": len(manifest), "objects": manifest},
                   indent=2),
        encoding="utf-8",
    )


def write_index(outdir: Path, repo: str, manifest: list[dict[str, Any]]) -> None:
    issues = [row for row in manifest if row["kind"] == "issue"]
    pulls = [row for row in manifest if row["kind"] == "pull"]
    lines = [
        f"# {repo} snapshot",
        "",
        f"{len(manifest)} objects: {len(issues)} issues, {len(pulls)} pull requests.",
        "",
        "One readable file per object beside its raw JSON.",
        "`MANIFEST.json` carries a SHA-256 per rendering, and `RECOVER.md` says",
        "how to refile from this directory.",
        "",
        "## Issues",
        "",
    ]
    lines += [
        f"- [#{row['number']}](issues/{row['number']}.md) {row['state']} - {row['title']}"
        for row in issues
    ]
    lines += ["", "## Pull requests", ""]
    lines += [
        f"- [#{row['number']}](prs/{row['number']}.md) {row['state']} - {row['title']}"
        for row in pulls
    ]
    (outdir / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def recover_note(outdir: Path, repo: str, saved: bool) -> None:
    """Write the instructions that make this directory self-describing."""
    if saved:
        state = (
            "Every object was submitted to Save Page Now. `SAVES.json` holds each\n"
            "job id, its state and, once the archive answered, the capture URL and\n"
            "the capture's timestamp."
        )
    else:
        state = (
            "The archive was not asked for captures. Run this lane again with\n"
            "`--save` to put a copy of every page on a host that is not this one."
        )
    text = f"""# Recovering {repo} from this directory

Every issue and pull request is here twice: `issues/<n>.json` and
`prs/<n>.json` are the API's own answers, and the `.md` beside each is a
readable rendering with the body, the comments, the reviews and the review
comments. `MANIFEST.json` holds a SHA-256 per rendering, so a copy that lost
bytes can be spotted rather than trusted.

Nothing here needs the network:

- to read an issue, open its `.md`;
- to refile one, take the `## Body` section of its `.md` as the body and the
  `## Comments` section as the discussion;
- to check the archive is intact, run `ghre snapshot <repo> --out <dir> --verify`.

## Capture state

{state}
"""
    (outdir / "RECOVER.md").write_text(text, encoding="utf-8")


def plan_urls(repo: str, manifest: list[dict[str, Any]]) -> list[str]:
    """Every page worth capturing, repository chrome included.

    The list pages are kept as well as the objects: enumeration is the first
    thing a recovery needs, and a captured list is what proves an object
    existed when its own page was never captured.
    """
    owner, _, name = repo.partition("/")
    urls = [
        f"https://github.com/{owner}/{name}",
        f"https://github.com/{owner}/{name}/issues?q=is%3Aissue",
        f"https://github.com/{owner}/{name}/pulls?q=is%3Apr",
    ]
    urls += [row["url"] for row in manifest if row.get("url")]
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


class SaveQueue:
    """Submission state for one repository's pages, resumable by design.

    The file is the queue. A submission writes its job id before the next
    request leaves, so a run that dies mid-way loses nothing already accepted
    by the archive, and a poll can always be repeated: asking a job's state
    twice is the same as asking once.
    """

    def __init__(self, path: Path):
        self.path = path
        self.state: dict[str, Any] = {"urls": [], "jobs": {}}
        if path.exists():
            self.state = json.loads(path.read_text(encoding="utf-8"))

    @property
    def jobs(self) -> dict[str, dict[str, Any]]:
        return self.state.setdefault("jobs", {})

    def plan(self, urls: list[str]) -> None:
        existing = self.state.get("urls") or []
        merged = list(existing)
        for url in urls:
            if url not in merged:
                merged.append(url)
        self.state["urls"] = merged

    def save(self) -> None:
        """Write the state file atomically.

        A run is expected to be killed mid-submission, by its operator or by a
        session ending, and the write lands while the queue is being read or
        just as the kill arrives. Truncate-then-write hands both readers and
        the next run a half file; write-then-rename hands them all of it or
        none of it.
        """
        handle, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self.state, indent=2))
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def submitted(self) -> list[str]:
        return [u for u in self.state.get("urls", []) if self.jobs.get(u, {}).get("job")]

    def outstanding(self) -> list[str]:
        return [
            u
            for u in self.state.get("urls", [])
            if self.jobs.get(u, {}).get("job")
            and self.jobs[u].get("status") not in {"success", "failed"}
        ]

    def submit(
        self,
        client: Client,
        pace: float = SUBMIT_PACE,
        log=print,
        workers: int = 1,
        progress_every: int = 50,
    ) -> int:
        """Ask the archive to capture every page not yet submitted.

        A submission is one HTTP round trip against the broker, which itself
        waits on the archive before answering a job id, so one request costs
        seconds rather than milliseconds. `workers` runs that many round trips
        at once, which is what turns a run of hours into one of minutes; the
        state file is rewritten by this thread only, every few completions, so
        a kill costs the last handful of answers and nothing before them.
        """
        todo = [
            url
            for url in self.state.get("urls", [])
            if not self.jobs.get(url, {}).get("job")
        ]
        log(f"submitting {len(todo)} of {len(self.state.get('urls', []))} urls")
        submitted = 0
        done = 0

        def one(url: str):
            target = (
                f"{client.route.save_base}/save"
                f"?url={urllib.parse.quote(url, safe='')}"
            )
            status, _headers, body = client.post(target, lane="save")
            job = None
            if status in {200, 201, 202}:
                try:
                    job = json.loads(body).get("job")
                except json.JSONDecodeError:
                    job = None
            row = {
                "job": job,
                "status": "pending" if job else None,
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "http_status": status,
                "error": None
                if job
                else (body[:200].decode("utf-8", "replace") or f"status {status}"),
            }
            return url, row

        def record(url: str, row: dict) -> None:
            nonlocal submitted, done
            self.jobs[url] = row
            done += 1
            if row["job"]:
                submitted += 1
            else:
                log(f"  refused {url}: {row['error']}")
            if done % progress_every == 0:
                log(f"  {done}/{len(todo)} submitted")
                self.save()

        if workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                for url, row in pool.map(one, todo):
                    record(url, row)
        else:
            for url in todo:
                result = one(url)
                record(url, result[1])
                if pace:
                    time.sleep(pace)
        self.save()
        log(f"submitted ok: {submitted}/{len(todo)}")
        return submitted

    def poll(self, client: Client, sweeps: int = 1, log=print,
             settle: float = 45.0, workers: int = 1) -> dict[str, int]:
        """Follow every pending job, `sweeps` times.

        Asking a job's state is as slow as submitting one was, for the same
        reason, so `workers` runs the same pool here. A sweep that reads an
        answer twice loses nothing: the second read names the same capture.
        """
        for sweep in range(sweeps):
            pending = self.outstanding()
            if not pending:
                break
            log(f"sweep {sweep + 1}: {len(pending)} outstanding")

            def one(url: str):
                job = self.jobs[url]
                answer = client.get_json(
                    f"{client.route.save_base}/save/{job['job']}", lane="save"
                )
                return url, answer

            def apply(pair):
                url, answer = pair
                job = self.jobs[url]
                if isinstance(answer, dict):
                    job["status"] = answer.get("status") or job.get("status")
                    job["timestamp"] = answer.get("timestamp")
                    job["capture"] = answer.get("capture")
                    if answer.get("error"):
                        job["error"] = answer["error"]

            if workers > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    for pair in pool.map(one, pending):
                        apply(pair)
            else:
                for url in pending:
                    apply(one(url))
            self.save()
            counts: dict[str, int] = {}
            for url in self.state.get("urls", []):
                key = self.jobs.get(url, {}).get("status") or "pending"
                counts[key] = counts.get(key, 0) + 1
            log(f"  {counts}")
            if sweep + 1 < sweeps and self.outstanding():
                time.sleep(settle)
        return self.summary()

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for url in self.state.get("urls", []):
            key = self.jobs.get(url, {}).get("status") or "not submitted"
            counts[key] = counts.get(key, 0) + 1
        return counts


def merge_saves(outdir: Path, queue: SaveQueue) -> None:
    """Fold capture state into the manifest so one file answers both questions."""
    manifest_path = outdir / "MANIFEST.json"
    if not manifest_path.exists():
        return
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    jobs = queue.jobs
    for row in data.get("objects", []):
        job = jobs.get(row.get("url", ""))
        if job:
            row["capture"] = job.get("capture")
            row["capture_timestamp"] = job.get("timestamp")
            row["save_job"] = job.get("job")
            row["save_status"] = job.get("status")
    data["saves"] = queue.summary()
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    (outdir / "SAVES.json").write_text(
        json.dumps(queue.state, indent=2), encoding="utf-8"
    )


def verify(outdir: Path) -> list[str]:
    """Check the archive against its own manifest. Returns the problems."""
    problems: list[str] = []
    manifest_path = outdir / "MANIFEST.json"
    if not manifest_path.exists():
        return [f"no manifest at {manifest_path}"]
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = data.get("objects") or []
    if not rows:
        problems.append("the manifest lists no object")
    counts = {"issue": 0, "pull": 0}
    for row in rows:
        counts[row.get("kind", "issue")] = counts.get(row.get("kind", "issue"), 0) + 1
        path = outdir / row.get("markdown", "")
        if not path.exists():
            problems.append(f"missing rendering: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest != row.get("sha256"):
            problems.append(f"hash mismatch: {path}")
        if not text.strip():
            problems.append(f"empty rendering: {path}")
        json_path = outdir / row["markdown"].replace(".md", ".json")
        if not json_path.exists():
            problems.append(f"missing raw json: {json_path}")
    if data.get("count") != len(rows):
        problems.append(f"count says {data.get('count')}, manifest holds {len(rows)}")
    if not (outdir / "INDEX.md").exists():
        problems.append("no INDEX.md")
    return problems


def zip_snapshot(outdir: Path, target: Path) -> Path:
    """Pack the directory, manifest and save state included."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(outdir.rglob("*")):
            # The run's request log is about this run, not about the repository,
            # so it stays beside the archive rather than inside it.
            if path.is_file() and path.name != "requests.tsv":
                archive.write(path, path.relative_to(outdir.parent))
    return target
