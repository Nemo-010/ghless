"""HTTP access and git transport, with a selectable route.

Every request goes through one Client so the budget, the retry rule and the
request log live in one place.

Two routes are supported, and the same code serves both:

- `pkgforge` (default) sends everything through a public fetch broker that
  forwards any URL, including the git smart-HTTP endpoints. That means a host
  that cannot reach GitHub or the Internet Archive directly can still run this
  tool, and the broker's own accounts are used for the API reads.
- `direct` talks to `api.github.com`, `github.com` and `web.archive.org`
  itself, using `GITHUB_TOKEN` or `GH_TOKEN` when one is set.

The route is chosen by `--transport` or `GHRE_TRANSPORT`. Nothing in this file
refers to any particular account, repository or organisation.

Rate limits differ by route, so they are stated where they matter: the broker
allows about 100 requests a minute per address and a git operation costs one or
two, while GitHub itself allows 60 requests an hour unauthenticated.
"""

from __future__ import annotations

import threading
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

USER_AGENT = "ghre/1.0"

# 429 is a hold or a spend breaker, 5xx is a deadline or an upstream fault.
# Both are worth one more attempt; a 404 never is.
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Route:
    """Where each kind of request goes under one transport."""

    name: str
    api_base: str
    git_base: str
    archive_base: str
    uses_broker: bool
    # Where a Save Page Now submission goes. It is the archive broker rather
    # than one archive: the broker speaks for four of them, needs no account,
    # and is the only one with a documented submission endpoint. A capture it
    # returns is keyed on the origin's own URL, so it outlives both the account
    # that wrote the page and the host that asked for the copy.
    save_base: str = "https://archive.web.pkgforge.dev"


PKGFORGE = Route(
    name="pkgforge",
    api_base="https://forge.web.pkgforge.dev/github",
    # A path-prefix proxy: git appends `/info/refs?service=git-upload-pack`
    # to whatever URL it is given, which lands on the broker's forwarding
    # path and comes back as a real ref advertisement.
    git_base="https://fetch.web.pkgforge.dev/https://github.com",
    archive_base="https://fetch.web.pkgforge.dev/https://web.archive.org",
    uses_broker=True,
)

DIRECT = Route(
    name="direct",
    api_base="https://api.github.com",
    git_base="https://github.com",
    archive_base="https://web.archive.org",
    uses_broker=False,
)

ROUTES = {route.name: route for route in (PKGFORGE, DIRECT)}


def resolve_route(name: str | None = None) -> Route:
    """Pick the route from an explicit name or the environment."""
    chosen = (name or os.environ.get("GHRE_TRANSPORT") or PKGFORGE.name).strip()
    route = ROUTES.get(chosen)
    if route is None:
        raise SystemExit(
            f"unknown transport {chosen!r}; choose one of "
            + ", ".join(sorted(ROUTES))
        )
    return route


@dataclass
class Budget:
    """Request accounting for one run."""

    limit: int = 200
    used: int = 0
    log_path: Path | None = None
    _rows: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def available(self) -> bool:
        return self.used < self.limit

    def spend(self, status: str, lane: str, url: str) -> None:
        with self._lock:
            self.used += 1
            row = f"{status}\t{lane}\t{url}"
            self._rows.append(row)
            if self.log_path is not None:
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(row + "\n")


class Client:
    """A polite HTTP client.

    A rejected token is dropped for the rest of the run rather than spent on
    every request, because a suspended account's token answers 403 while the
    public endpoints still work unauthenticated.
    """

    def __init__(
        self,
        budget: Budget | None = None,
        timeout: int = 45,
        token: str | None = None,
        route: Route | str | None = None,
    ):
        self.budget = budget or Budget()
        self.timeout = timeout
        self.route = route if isinstance(route, Route) else resolve_route(route)
        if token is None:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        self._token = token.strip()
        self._token_ok = bool(self._token) and not self.route.uses_broker

    # -- endpoints --------------------------------------------------------

    def git_url(self, owner: str, repo: str) -> str:
        """The clone URL for a repository under the current route."""
        return f"{self.route.git_base}/{owner}/{repo}"

    # -- plumbing ---------------------------------------------------------

    def _headers(self, url: str) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/vnd.github+json"
                if url.startswith(self.route.api_base)
                else "*/*"
            ),
        }
        if self._token_ok:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get(
        self, url: str, lane: str, timeout: int | None = None
    ) -> tuple[int, bytes]:
        """Return (status, body). Status 0 means no answer at all."""
        attempt = 0
        pause = 5
        while True:
            if not self.budget.available():
                return 0, b""
            request = urllib.request.Request(url, headers=self._headers(url))
            try:
                with urllib.request.urlopen(
                    request, timeout=timeout or self.timeout
                ) as response:
                    body = response.read()
                    self.budget.spend(str(response.status), lane, url)
                    return response.status, body
            except urllib.error.HTTPError as exc:
                body = exc.read() if exc.fp else b""
                self.budget.spend(str(exc.code), lane, url)
                # A suspended or revoked token answers 403 with a message that
                # says so. Dropping it lets the public endpoints keep working.
                if exc.code in {401, 403} and self._token_ok and (
                    b"suspend" in body.lower() or b"bad credentials" in body.lower()
                ):
                    self._token_ok = False
                    continue
                if exc.code not in RETRY_STATUS:
                    return exc.code, body
                attempt += 1
                if attempt >= 3:
                    return exc.code, body
            except (urllib.error.URLError, TimeoutError, OSError):
                self.budget.spend("000", lane, url)
                attempt += 1
                if attempt >= 3:
                    return 0, b""
            time.sleep(pause)
            pause = min(pause * 2, 30)

    def post(
        self, url: str, lane: str, timeout: int | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        """POST with no body. Returns (status, headers, body).

        The headers come back because the archive's refusals are read from
        them: a 429 names its own wait in `Retry-After`, and retrying on a
        guessed interval is how a polite client becomes a nuisance.
        """
        attempt = 0
        pause = 5
        while True:
            if not self.budget.available():
                return 0, {}, b""
            request = urllib.request.Request(
                url, headers=self._headers(url), method="POST", data=b""
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=timeout or self.timeout
                ) as response:
                    body = response.read()
                    self.budget.spend(str(response.status), lane, url)
                    return response.status, dict(response.headers), body
            except urllib.error.HTTPError as exc:
                body = exc.read() if exc.fp else b""
                self.budget.spend(str(exc.code), lane, url)
                headers = dict(exc.headers or {})
                if exc.code not in RETRY_STATUS:
                    return exc.code, headers, body
                attempt += 1
                if attempt >= 4:
                    return exc.code, headers, body
                wait = headers.get("Retry-After")
                try:
                    pause = max(int(wait), 1) if wait else pause
                except ValueError:
                    pass
            except (urllib.error.URLError, TimeoutError, OSError):
                self.budget.spend("000", lane, url)
                attempt += 1
                if attempt >= 4:
                    return 0, {}, b""
            time.sleep(pause)
            pause = min(pause * 2, 30)

    def github_list(
        self, path: str, lane: str = "api", pause: float = 0.0
    ) -> list[dict[str, Any]]:
        """Every page of a GitHub REST list, flattened.

        Paged by number rather than by `Link`, because a broker that rewrites
        headers drops the relation while the page counter still works.
        """
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            chunk = self.github(f"{path}{sep}per_page=100&page={page}", lane)
            if not isinstance(chunk, list) or not chunk:
                break
            rows.extend(item for item in chunk if isinstance(item, dict))
            if len(chunk) < 100:
                break
            page += 1
            if pause:
                time.sleep(pause)
        return rows

    def get_json(self, url: str, lane: str, timeout: int | None = None) -> Any:
        status, body = self.get(url, lane, timeout)
        if status != 200 or not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def api(self, url: str, lane: str, timeout: int | None = None) -> Any:
        """A JSON answer that distinguishes a refusal from a negative.

        Returns `{"_ghre_status": <code>}` instead of None when the request
        produced no JSON, so a caller can tell a 404 from a spent budget and
        record the right one. A real 404 body parses normally and keeps its
        `message` field.
        """
        status, body = self.get(url, lane, timeout)
        if body:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = None
            if data is not None:
                return data
        return {"_ghre_status": status}

    def get_text(
        self, url: str, lane: str, timeout: int | None = None
    ) -> str | None:
        status, body = self.get(url, lane, timeout)
        if status != 200 or not body:
            return None
        return body.decode("utf-8", errors="replace")

    def github(self, path: str, lane: str = "api") -> Any:
        """GitHub REST JSON. `path` starts after the API host."""
        return self.api(f"{self.route.api_base}/{path.lstrip('/')}", lane)

    # -- archive ----------------------------------------------------------

    def cdx(
        self,
        prefix: str,
        lane: str = "cdx",
        limit: int = 200,
        timeout: int = 90,
        attempts: int = 3,
        save_to: Path | None = None,
    ) -> list[list[str]] | None:
        """Wayback CDX prefix query.

        A prefix query lists every archived URL under a path, including query
        strings no guessed URL would contain, which makes it the widest index
        of what was captured. A wide prefix can be slow; a 5xx is a timeout,
        not an empty index, so it is retried with a longer deadline.
        """
        target = (
            f"{self.route.archive_base}/cdx/search/cdx"
            f"?url={urllib.parse.quote(prefix, safe='')}"
            f"&matchType=prefix&output=json&limit={limit}&collapse=urlkey"
        )
        span = timeout
        for attempt in range(attempts):
            status, body = self.get(target, lane, span)
            data = None
            if body:
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    data = None
            # The raw answer is evidence: an empty array and a failed query
            # must stay distinguishable after the run.
            if save_to is not None:
                save_to.parent.mkdir(parents=True, exist_ok=True)
                header = f"# status {status} attempts {attempt + 1}\n".encode()
                save_to.write_bytes(header + (body or b""))
            if isinstance(data, list) and data:
                return data[1:]
            if isinstance(data, list):
                return []
            span = int(span * 1.5)
            time.sleep(5)
        return None

    def captures(
        self, url: str, lane: str = "archive"
    ) -> list[dict[str, Any]] | None:
        """Captures of one exact URL, from the Wayback availability API."""
        data = self.get_json(
            "https://archive.org/wayback/available?"
            + urllib.parse.urlencode({"url": url}),
            lane,
        )
        if not isinstance(data, dict):
            return None
        closest = (data.get("archived_snapshots") or {}).get("closest")
        if not closest:
            return []
        return [
            {
                "timestamp": closest.get("timestamp", ""),
                "original_url": url,
                "status": closest.get("status", ""),
                "replay": closest.get("url", ""),
            }
        ]

    def replay(
        self, timestamp: str, url: str, lane: str = "replay", timeout: int = 90
    ) -> str | None:
        """An archived page's original bytes, without the Wayback toolbar.

        The `id_` suffix asks for the capture as stored rather than as
        rewritten, which is what makes a captured commit page's embedded JSON
        parseable.
        """
        return self.get_text(
            f"{self.route.archive_base}/web/{timestamp}id_/{url}", lane, timeout
        )


def parse_front_matter(text: str) -> dict[str, str]:
    """Split a flat `key: value` header block off a document."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    return meta


def body_of(text: str) -> str:
    """The content after a front matter block, if there is one."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2] if len(parts) == 3 else text
