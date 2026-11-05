"""The read-only backend every command shares.

Two jobs: turn a proxy answer into data (or an error phrased the way gh phrases
it), and follow Github's `Link` paging so callers can ask for N items without
knowing how many pages that is.
"""

from __future__ import annotations

import json

from . import http
from .errors import GhError, NetworkError

MAX_PER_PAGE = 100


class ApiError(GhError):
    """GitHub answered, and the answer is an error."""

    def __init__(self, status, message, body=None, *, url=None):
        super().__init__(message, body=body, status=status)
        self.url = url


class Backend:
    def __init__(self, client=None, debug=False):
        self.client = client or http.Client(debug=debug)

    # -- one request ---------------------------------------------------
    def response(self, path, params=None, headers=None):
        return self.client.github(path, params, headers)

    def data(self, path, params=None, headers=None):
        resp = self.response(path, params, headers)
        return self._decode(resp)

    def _decode(self, resp):
        if 200 <= resp.status < 300 or resp.status == 304:
            if resp.status == 304:
                return None
            ctype = (resp.header("content-type") or "").lower()
            if "json" not in ctype and resp.body.lstrip()[:1] not in (b"{", b"["):
                return resp.text()
            try:
                return json.loads(resp.body.decode("utf-8"))
            except ValueError:
                raise NetworkError("the forge answered something that is not JSON")
        problem = resp.problem()
        if problem:
            reason = problem.get("reason")
            message = problem.get("detail") or problem.get("title") or "request failed"
            hint = problem.get("hint")
            if not hint:
                hint = _reason_hint(reason, problem)
            raise ApiError(resp.status, message, resp.body, url=resp.url) if not hint else \
                _with_hint(ApiError(resp.status, message, resp.body, url=resp.url), hint)
        # A forge error, passed through: GitHub's own body and message.
        message = None
        try:
            doc = json.loads(resp.body.decode("utf-8"))
            if isinstance(doc, dict):
                message = doc.get("message")
                errors = doc.get("errors")
                if errors and isinstance(errors, list):
                    detail = "; ".join(
                        str(item.get("message") if isinstance(item, dict) else item)
                        for item in errors)
                    if detail:
                        message = "%s (%s)" % (message, detail)
        except ValueError:
            text = resp.text().strip()
            message = text.splitlines()[0] if text else None
        raise ApiError(resp.status, message or "HTTP %d" % resp.status,
                       resp.body, url=resp.url)

    # -- pages ---------------------------------------------------------
    def pages(self, path, params=None, headers=None, limit=None):
        """Yield pages of JSON answers, up to `limit` items where it applies."""
        params = list(params or [])
        if not any(key == "per_page" for key, _ in params):
            params.append(("per_page", str(min(MAX_PER_PAGE, limit or MAX_PER_PAGE))))
        seen = 0
        first = True
        resp = self.response(path, params, headers)
        while True:
            if first:
                page = self._decode(resp)
                first = False
            else:
                page = self._decode(resp)
            yield page
            if resp.status >= 400:
                return
            items = page if isinstance(page, list) else (page or {}).get("items")
            if limit is not None and isinstance(page, list):
                seen += len(page)
                if seen >= limit:
                    return
            nxt = resp.link_next()
            if not nxt:
                return
            if nxt.startswith("/"):
                nxt = self.client.base + nxt
            elif not nxt.startswith("http"):
                nxt = self.client.base + "/github/" + nxt.lstrip("/")
            if not nxt.startswith(self.client.base + "/"):
                return
            resp = self.client.request("GET", nxt, headers=headers)

    def collect(self, path, params=None, headers=None, limit=None, keep=None):
        """Items across pages, stopping as soon as `limit` of them are kept.

        `keep` is what makes a filtered list still stop early: pages are only
        followed while the answer is short of `limit`, so a repo with 10k
        closed issues costs one page per 100 wanted items and no more.
        """
        out = []
        for page in self.pages(path, params, headers):
            if isinstance(page, list):
                batch = page
            elif isinstance(page, dict) and isinstance(page.get("items"), list):
                batch = page["items"]
            else:
                return page
            for item in batch:
                if keep and not keep(item):
                    continue
                out.append(item)
                if limit is not None and len(out) >= limit:
                    return out
        return out

    def list(self, path, params=None, headers=None, limit=None, keep=None):
        """All items across pages, trimmed to `limit`."""
        return self.collect(path, params, headers, limit=limit, keep=keep)

    def search(self, path, query, *, sort=None, order=None, limit=30,
               headers=None, params=None):
        """Search endpoints answer {total_count, items, ...} and page at 100."""
        merged = None
        collected = []
        lowered = min(limit, 1000)
        base_params = [("q", query), ("per_page", str(min(100, lowered)))]
        if sort and sort != "best-match":
            base_params.append(("sort", sort))
        if order:
            base_params.append(("order", order))
        base_params.extend(params or [])
        for page in self.pages(path, base_params, headers, limit=lowered):
            if not isinstance(page, dict) or "items" not in page:
                if merged is None:
                    merged = page
                    break
            if merged is None:
                merged = dict(page)
            collected.extend(page.get("items") or [])
            if len(collected) >= lowered:
                break
        if not isinstance(merged, dict):
            return merged
        merged["items"] = collected[:lowered]
        merged["incomplete_results"] = merged.get("incomplete_results", False)
        return merged


def _with_hint(error, hint):
    error.hint = hint
    return error


def _reason_hint(reason, problem):
    if reason == "own-account":
        return "the forge service reads public resources only"
    if reason in ("held", "rate-limited", "disabled"):
        after = problem.get("retry_after") or problem.get("retryAfter")
        return "retry later" if not after else "retry after %s seconds" % after
    if reason in ("none-configured", "permanently-exhausted"):
        return "the forge service has no account configured for that read"
    if reason == "graphql-refused":
        return "only read queries can be proxied"
    if reason == "not-found":
        return "check the repository, number or path"
    return None
