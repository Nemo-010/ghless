"""How answers are printed: JSON, --jq, --template, --include, text tables."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from .errors import GhError, UsageError


def dumps(obj, indent=None):
    """Compact JSON, the way gh prints API bodies and --json output."""
    if indent is None:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return json.dumps(obj, indent=indent, ensure_ascii=False)


def write(text):
    if text is None:
        return
    out = sys.stdout
    out.write(text)
    if not text.endswith("\n"):
        out.write("\n")


def write_raw(text):
    if text:
        sys.stdout.write(text)


# -- jq ------------------------------------------------------------------

def _jq_with_binary(expr, data):
    jq = shutil.which("jq")
    if not jq:
        return None
    try:
        proc = subprocess.run([jq, "-c", expr], input=json.dumps(data).encode(),
                              capture_output=True)
    except OSError:
        return None
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        raise GhError("could not parse jq expression: " + message)
    lines = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith('"'):
            try:
                value = json.loads(line)
            except ValueError:
                lines.append(line)
                continue
            if isinstance(value, str):
                lines.append(value)
                continue
        lines.append(line)
    return "".join(line + "\n" for line in lines)


def jq(expr, data):
    """Filter `data` with jq syntax and return gh's exact output text."""
    from . import jqish
    try:
        return jqish.run(expr, data)
    except jqish.JqError as exc:
        external = _jq_with_binary(expr, data)
        if external is not None:
            return external
        # gojq's own text, printed bare the way gh prints it.
        raise GhError(str(exc), bare=True)


# -- Go templates --------------------------------------------------------

def template(tpl, data):
    from . import gotmpl
    try:
        return gotmpl.render(tpl, data)
    except gotmpl.TemplateError as exc:
        raise GhError(str(exc))


# -- response printing ---------------------------------------------------

def print_response(resp, *, jq_expr=None, tpl=None, silent=False,
                   include=False, verbose=False):
    body = resp.body
    if silent:
        return
    text = body.decode("utf-8", "replace")
    looks_json = _is_json(resp)
    prefix = _headers_block(resp) if include else ""
    if jq_expr is not None or tpl is not None:
        if not looks_json:
            raise UsageError("the response is not JSON; --jq/--template cannot be used")
        data = json.loads(text)
        out = jq(jq_expr, data) if jq_expr is not None else template(tpl, data)
        write_raw(prefix + out)
        return
    if include:
        write_raw(prefix + text)
        return
    if looks_json and os.environ.get("GH_FORGE_PRETTY_JSON"):
        write(dumps(json.loads(text), indent=2))
        return
    # gh writes the answer through byte for byte: no newline of its own.
    write_raw(text)


def _is_json(resp):
    ctype = (resp.header("content-type") or "").lower()
    if "json" in ctype:
        return True
    if "text/" in ctype or "octet-stream" in ctype or "html" in ctype:
        return False
    head = resp.body.lstrip()[:1]
    return head in (b"{", b"[")


def _headers_block(resp):
    from .http import Response  # noqa: F401
    lines = []
    reason = {200: "OK", 201: "Created", 204: "No Content", 304: "Not Modified",
              400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
              404: "Not Found", 422: "Unprocessable Entity",
              429: "Too Many Requests", 500: "Internal Server Error",
              502: "Bad Gateway", 503: "Service Unavailable"}.get(resp.status, "")
    lines.append("HTTP/2.0 %d %s" % (resp.status, reason) if reason
                 else "HTTP/2.0 %d" % resp.status)
    skip = {"connection", "content-length", "transfer-encoding", "nel", "report-to",
            "alt-svc", "cf-ray", "cf-cache-status", "server", "date"}
    for key in sorted(resp.headers):
        if key in skip:
            continue
        lines.append("%s: %s" % (_title_header(key), resp.headers[key]))
    return "\n".join(lines) + "\n\n"


def _title_header(name):
    special = {"etag": "ETag", "x-github-request-id": "X-GitHub-Request-Id"}
    if name in special:
        return special[name]
    return "-".join(part.capitalize() for part in name.split("-"))


# -- tables --------------------------------------------------------------

def table(rows):
    """Render tab-separated columns: `gh`'s list output is literally tabs."""
    if not rows:
        return ""
    lines = []
    for row in rows:
        lines.append("\t".join("" if cell is None else str(cell) for cell in row))
    return "\n".join(lines) + "\n"
