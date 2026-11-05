"""Pieces several commands need: repo resolution, output, small formatters."""

from __future__ import annotations

import datetime
import json

from .. import output, repoctx
from ..errors import UsageError


def repo_for(ctx, opts, *, positional=None, required=True):
    explicit = positional or opts.get("repo")
    return ctx.repo(explicit, required=required)


def json_supported(opts):
    return any(key in opts for key in ("json", "jq", "template"))


def emit(obj, opts, command=None, fieldset=None):
    """Print one command's answer the way gh prints it.

    `obj` is already gh-shaped; when a --json field list was given it is
    projected and validated first.
    """
    from .. import fields
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    if requested:
        if isinstance(obj, list):
            obj = [fields.select(item, requested, command) for item in obj]
        elif isinstance(obj, dict):
            obj = fields.select(obj, requested, command)
        else:
            obj = fields.select({}, requested, command)
        # gh marshals --json output from a map, so the keys come out sorted;
        # nested values are structs and keep their own field order.
        obj = _sorted(obj)
    data = obj
    if opts.get("jq") is not None:
        output.write_raw(output.jq(opts["jq"], data))
    elif opts.get("template") is not None:
        output.write_raw(output.template(opts["template"], data))
    else:
        output.write(output.dumps(data))


def _sorted(value):
    """gh marshals the requested --json fields from a Go map: the top level is
    sorted; nested values are structs and keep their own field order."""
    if isinstance(value, list):
        return [_sorted(item) for item in value]
    if isinstance(value, dict):
        return {key: value[key] for key in sorted(value)}
    return value


def _sorted_deep(value):
    """gh's search JSON is built from generic maps: sorted at every level."""
    if isinstance(value, list):
        return [_sorted_deep(item) for item in value]
    if isinstance(value, dict):
        return {key: _sorted_deep(value[key]) for key in sorted(value)}
    return value


def plain_emit(obj):
    output.write(output.dumps(obj))


def person(ctx, doc):
    """{login, name} with the display name filled in, like gh shows it.

    REST gives a login; the display name lives on /users/<login>. One lookup
    per distinct login per run, skipped quietly when the lookup fails.
    """
    if not doc:
        return ""
    login = doc.get("login") or ""
    if not login:
        return ""
    name = doc.get("name") or ctx.display_name(login)
    return "%s (%s)" % (login, name) if name else login


def person_list(ctx, users):
    return ", ".join(filter(None, (person(ctx, user) for user in users or [])))


def label_names(doc):
    return ", ".join(l.get("name") or "" for l in (doc.get("labels") or []))


def duration_text(start, end):
    """gh's run duration: 50s, 2m14s, 1h3m."""
    if not start or not end:
        return ""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    try:
        begin = datetime.datetime.strptime(start, fmt)
        finish = datetime.datetime.strptime(end, fmt)
    except ValueError:
        return ""
    seconds = int((finish - begin).total_seconds())
    if seconds < 0:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%dh%dm" % (hours, minutes)
    if minutes:
        return "%dm%ds" % (minutes, secs)
    return "%ds" % secs


def parse_csv(values):
    """--label takes comma lists and repeats; accept both shapes."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out = []
    for value in values:
        for chunk in str(value).split(","):
            chunk = chunk.strip()
            if chunk:
                out.append(chunk)
    return out


def require_int(text, what="number"):
    try:
        return int(str(text).lstrip("#"))
    except (TypeError, ValueError):
        raise UsageError("invalid %s: %r" % (what, text))


def no_repo_error():
    return UsageError("no repository found: run inside a checkout or pass -R OWNER/REPO")
