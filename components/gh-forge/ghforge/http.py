"""HTTP transport for the forge read-only proxy.

The proxy (`https://forge.web.pkgforge.dev/github/<rest path>`) answers with
GitHub's own status, headers and body, and with an RFC 9457 problem for its own
failures. This module is the only place that knows which failures are worth a
retry: the retry classes come from the service's published pipeline contract.

    retry after Retry-After : 429 held; 503 disabled; 429 rate-limited
    retry with backoff      : 502 upstream-failed/upstream-unreachable;
                              503 deadline/budget; 504 deadline
    retry only if retryable : 502 providers-failed
    never retry             : every 4xx the service builds itself
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .errors import NetworkError

DEFAULT_BASE = "https://forge.web.pkgforge.dev"
DEFAULT_TIMEOUT = 45
MAX_ATTEMPTS = 4
MAX_SLEEP = 20.0

# Headers the proxy owns; a caller must not smuggle a credential through it.
_STRIPPED_HEADERS = {"authorization", "cookie", "proxy-authorization"}


def base_url():
    return (os.environ.get("GH_FORGE_BASE") or DEFAULT_BASE).rstrip("/")


def cache_dir():
    root = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    d = os.path.join(root, "gh-forge")
    return d


class Response:
    __slots__ = ("status", "headers", "body", "url", "cached")

    def __init__(self, status, headers, body, url, cached=False):
        self.status = status
        self.headers = headers          # lower-case key -> value
        self.body = body                # bytes
        self.url = url
        self.cached = cached

    def header(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def text(self):
        return self.body.decode("utf-8", "replace")

    def problem(self):
        """The service's own RFC 9457 problem, or None for a forge answer."""
        ctype = self.header("content-type", "")
        if "application/problem+json" in ctype:
            try:
                return self.json()
            except ValueError:
                return {"title": self.text()[:200]}
        # Some edges answer the problem without the media type; only trust it
        # when the shape is unmistakable (status+reason, and no `message`).
        if self.status >= 400 and "json" in ctype:
            try:
                doc = self.json()
            except ValueError:
                return None
            if isinstance(doc, dict) and "reason" in doc and "message" not in doc:
                return doc
        return None

    def link_next(self):
        link = self.header("link")
        return parse_link(link, "next")


def parse_link(header, rel):
    """Pull one rel out of an RFC 8288 Link header (GitHub's or the proxy's)."""
    if not header:
        return None
    for part in header.split(","):
        section = part.strip()
        if not section.startswith("<"):
            continue
        end = section.find(">")
        if end < 0:
            continue
        url = section[1:end]
        rest = section[end + 1:]
        for param in rest.split(";"):
            param = param.strip()
            if param.startswith("rel="):
                value = param[4:].strip().strip('"')
                if rel in value.split():
                    return url
    return None


def retry_after_seconds(value):
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(value)
        return max(0.0, when.timestamp() - time.time())
    except Exception:
        return None


def _debug(msg):
    if os.environ.get("GH_FORGE_DEBUG"):
        print("gh-forge: " + msg, file=sys.stderr)


def _encode(params):
    """Public alias: callers build the same query string the client sends."""
    if not params:
        return ""
    pairs = []
    for key, value in params:
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                pairs.append((key, str(item)))
        else:
            pairs.append((key, str(value)))
    if not pairs:
        return ""
    return "?" + urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote, safe=":,")


class Client:
    """A thin, retrying GET client for the proxy."""

    def __init__(self, base=None, timeout=DEFAULT_TIMEOUT, debug=False):
        self.base = (base or base_url()).rstrip("/")
        self.timeout = timeout
        self.debug = debug or bool(os.environ.get("GH_FORGE_DEBUG"))

    # -- request -------------------------------------------------------
    def request(self, method, url, headers=None, body=None, attempts=MAX_ATTEMPTS):
        headers = {k: v for k, v in (headers or {}).items()
                   if k.lower() not in _STRIPPED_HEADERS}
        if not any(k.lower() == "user-agent" for k in headers):
            headers["User-Agent"] = "gh-forge/" + _version()
        data = body
        last = None
        for attempt in range(1, attempts + 1):
            if self.debug:
                _debug("%s %s (attempt %d)" % (method, url, attempt))
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as raw:
                    resp = _read(raw, url)
            except urllib.error.HTTPError as exc:
                resp = _read(exc, url)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                if attempt == attempts:
                    raise NetworkError(
                        "could not reach the forge service: %s" % _reason(exc),
                        hint="check GH_FORGE_BASE and the network; retry later",
                    ) from exc
                time.sleep(_backoff(attempt))
                continue

            if resp.status < 400 or resp.status == 304:
                return resp
            delay = self._retry_delay(resp)
            if delay is None or attempt == attempts:
                return resp
            if delay > MAX_SLEEP:
                # Do not block the caller for a long window; say when to retry.
                raise NetworkError(
                    "%s (HTTP %d)" % (self._problem_message(resp), resp.status),
                    hint="the service asked to retry after %ds" % int(delay),
                    body=resp.body, status=resp.status)
            if self.debug:
                _debug("HTTP %d, sleeping %.1fs" % (resp.status, delay))
            time.sleep(delay)
        raise NetworkError("giving up after %d attempts" % attempts)

    def _retry_delay(self, resp):
        problem = resp.problem()
        reason = (problem or {}).get("reason")
        status = resp.status
        if status == 429:
            return retry_after_seconds(resp.header("retry-after")) or 5.0
        if status == 503:
            if reason in ("disabled", "deadline", "budget", "rate-limited"):
                return retry_after_seconds(resp.header("retry-after")) or 5.0
            return None
        if status in (502, 504):
            if reason == "providers-failed" and problem and not problem.get("retryable"):
                return None
            return _backoff(2)
        return None

    def _problem_message(self, resp):
        problem = resp.problem()
        if problem:
            return problem.get("detail") or problem.get("title") or "request failed"
        try:
            doc = resp.json()
            if isinstance(doc, dict) and doc.get("message"):
                return str(doc["message"])
        except ValueError:
            pass
        text = resp.text().strip()
        return text.splitlines()[0] if text else "request failed"

    # -- routes --------------------------------------------------------
    def github(self, path, params=None, headers=None, method="GET", body=None):
        path = path.lstrip("/")
        url = self.base + "/github/" + path + _encode(params)
        return self.request(method, url, headers=headers, body=body)

    def github_pages(self, path, params=None, headers=None, limit=None,
                     per_page=None, into=None):
        """Walk the proxy's `Link: rel="next"` pages, yielding each Response."""
        params = list(params or [])
        if per_page:
            params = [(k, v) for k, v in params if k != "per_page"]
            params.append(("per_page", str(per_page)))
        resp = self.github(path, params, headers)
        page = 0
        while True:
            yield resp
            page += 1
            if resp.status >= 400:
                return
            nxt = resp.link_next()
            if not nxt or (limit is not None and into is not None and len(into) >= limit):
                return
            if nxt.startswith("/"):
                nxt = self.base + nxt
            elif not nxt.startswith("http"):
                nxt = self.base + "/github/" + nxt.lstrip("/")
            # Never follow a next that left the service: it would carry the
            # caller's IP to a third host and would not be the proxy's answer.
            if not nxt.startswith(self.base + "/"):
                _debug("ignoring off-service next link: %s" % nxt)
                return
            resp = self.request("GET", nxt, headers=headers)


def _read(raw, url):
    try:
        status = getattr(raw, "status", None) or raw.getcode()
    except Exception:
        status = 0
    headers = {}
    try:
        items = raw.headers.items()
    except Exception:
        items = []
    for key, value in items:
        lowered = key.lower()
        if lowered == "link" and lowered in headers:
            headers[lowered] = headers[lowered] + ", " + value
        else:
            headers[lowered] = value
    try:
        body = raw.read()
    except Exception:
        body = b""
    if not isinstance(body, bytes):
        body = bytes(body)
    return Response(status, headers, body, url)


def _reason(exc):
    reason = getattr(exc, "reason", exc)
    return str(reason) or exc.__class__.__name__


def _backoff(attempt):
    base = min(0.5 * (2 ** (attempt - 1)), 8.0)
    return base * (0.7 + random.random() * 0.6)


def _version():
    from . import __version__
    return __version__


# -- response cache ------------------------------------------------------

class Cache:
    """`gh api --cache 1h`, stored beside the mode decision."""

    def __init__(self, root=None):
        self.root = root or os.path.join(cache_dir(), "http")

    @staticmethod
    def parse_duration(value):
        if not value:
            return None
        text = str(value).strip()
        units = {"s": 1, "m": 60, "h": 3600}
        try:
            if text and text[-1] in units:
                return float(text[:-1]) * units[text[-1]]
            return float(text)
        except ValueError:
            return None

    def _path(self, url, accept):
        key = hashlib.sha256((url + "\n" + (accept or "")).encode()).hexdigest()
        return os.path.join(self.root, key + ".json")

    def get(self, url, accept):
        path = self._path(url, accept)
        try:
            with open(path, "rb") as handle:
                doc = json.loads(handle.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        if doc.get("expires", 0) < time.time():
            return None
        return Response(doc["status"], doc["headers"],
                        doc["body"].encode("utf-8", "surrogateescape"),
                        doc["url"], cached=True)

    def put(self, url, accept, resp, ttl):
        if not ttl:
            return
        os.makedirs(self.root, exist_ok=True)
        doc = {
            "expires": time.time() + ttl,
            "status": resp.status,
            "headers": resp.headers,
            "body": resp.body.decode("utf-8", "surrogateescape"),
            "url": resp.url,
        }
        tmp = self._path(url, accept) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(doc, handle)
        os.replace(tmp, self._path(url, accept))


encode_query = _encode
