"""`gh api`: a thin layer over the proxy's /github/<rest path> route."""

from __future__ import annotations

import json
import sys
import urllib.parse

from .. import http as httpmod
from .. import output, repoctx
from ..errors import GhError, Unsupported, UsageError
from ..flags import Spec, split

USAGE_ERROR_BLOCK = """

Usage:  gh api <endpoint> [flags]

Flags:
      --allow-escape-sequences   Allow printing terminal escape sequences
      --cache duration           Cache the response, e.g. "3600s", "60m", "1h"
  -F, --field key=value          Add a typed parameter in key=value format (use "@<path>" or "@-" to read value from file or stdin)
  -H, --header key:value         Add a HTTP request header in key:value format
      --hostname string          The GitHub hostname for the request (default "github.com")
  -i, --include                  Include HTTP response status line and headers in the output
      --input file               The file to use as body for the HTTP request (use "-" to read from standard input)
  -q, --jq string                Query to select values from the response using jq syntax
  -X, --method string            The HTTP method for the request (default "GET")
      --paginate                 Make additional HTTP requests to fetch all pages of results
  -p, --preview strings          Opt into GitHub API previews (names should omit '-preview')
  -f, --raw-field key=value      Add a string parameter in key=value format
      --silent                   Do not print the response body
      --slurp                    Use with "--paginate" to return an array of all pages of either JSON arrays or objects
  -t, --template string          Format JSON output using a Go template; see "gh help formatting"
      --verbose                  Include full HTTP request and response in the output
  """

USAGE = """gh api <endpoint> [flags]

Flags:
  -X, --method string        The HTTP method for the request (default "GET")
  -f, --raw-field key=value  Add a string parameter in key=value format
  -F, --field key=value      Add a typed parameter in key=value format
  -H, --header key:value     Add a HTTP request header in key:value format
  -i, --include              Include HTTP response status line and headers in the output
      --input file           The file to use as body for the HTTP request
  -q, --jq string            Query to select values from the response using jq syntax
  -p, --preview strings      Opt into GitHub API previews
  -t, --template string      Format JSON output using a Go template
      --cache duration       Cache the response, e.g. "3600s", "60m", "1h"
      --paginate             Make additional HTTP requests to fetch all pages of results
      --silent               Do not print the response body
      --slurp                Use with "--paginate" to return an array of all pages
      --verbose              Include full HTTP request and response in the output
      --hostname string      The GitHub hostname for the request (default "github.com")
"""


def spec():
    s = Spec()
    s.value("method", "X")
    s.value("raw-field", "f")     # accumulated by hand: repeated + key=value
    s.value("field", "F")
    s.value("header", "H")
    s.value("input")
    s.value("jq", "q")
    s.value("preview", "p")
    s.value("template", "t")
    s.value("cache")
    s.value("hostname")
    s.flag("include", "i")
    s.flag("paginate")
    s.flag("silent")
    s.flag("slurp")
    s.flag("verbose")
    s.flag("help", "h")
    return s


def _collect_pairs(argv):
    """Pull repeated -f/-F/-H/-p pairs out before the generic pass.

    gh accumulates these, and each one may be `k=v` with the value holding `=`.
    """
    pairs = {"raw-field": [], "field": [], "header": [], "preview": []}
    rest = []
    index = 0
    longs = {"--raw-field": "raw-field", "--field": "field",
             "--header": "header", "--preview": "preview"}
    shorts = {"f": "raw-field", "F": "field", "H": "header", "p": "preview"}
    while index < len(argv):
        token = argv[index]
        name = None
        value = None
        if token.startswith("--"):
            key, eq, inline = token.partition("=")
            if key in longs:
                name = longs[key]
                if eq:
                    value = inline
                    index += 1
                else:
                    if index + 1 >= len(argv):
                        raise UsageError("flag needs an argument: " + key)
                    value = argv[index + 1]
                    index += 2
        elif token.startswith("-") and len(token) > 1 and not token.startswith("--"):
            body = token[1:]
            key = body[0]
            if key in shorts:
                name = shorts[key]
                if len(body) > 1:
                    value = body[1:]
                    index += 1
                else:
                    if index + 1 >= len(argv):
                        raise UsageError("flag needs an argument: -" + key)
                    value = argv[index + 1]
                    index += 2
        if name is None:
            rest.append(token)
            index += 1
            continue
        if name == "header":
            key, sep, val = value.partition(":")
            if not sep:
                raise UsageError('invalid header: %r (expected "key:value")' % value)
            pairs["header"].append((key.strip(), val.strip()))
        elif name == "preview":
            pairs["preview"].append(value)
        else:
            key, sep, val = value.partition("=")
            if not sep:
                raise UsageError('invalid parameter: %r (expected "key=value")' % value)
            if val == "@-":
                val = sys.stdin.read()
            elif val.startswith("@"):
                with open(val[1:], "r", encoding="utf-8") as handle:
                    val = handle.read()
            pairs[name].append((key, val))
    return pairs, rest


def run(argv, ctx):
    if "--help" in argv or "-h" in argv:
        output.write(USAGE)
        return 0

    given, rest = _collect_pairs(argv)
    opts, args = split(rest, spec())
    if not args:
        raise UsageError("accepts 1 arg(s), received 0")
    if len(args) > 1:
        raise UsageError("accepts 1 arg(s), received %d" % len(args))

    if opts.get("slurp") and (opts.get("jq") is not None or opts.get("template") is not None):
        raise UsageError("the `--slurp` option is not supported with `--jq` or `--template`"
                         + USAGE_ERROR_BLOCK)

    endpoint = args[0]
    method = (opts.get("method") or "GET").upper()
    host = opts.get("hostname") or "github.com"
    if host not in ("github.com", "api.github.com"):
        raise Unsupported("the forge service reads github.com only, not %r" % host)

    headers = {"Accept": "application/vnd.github+json"}
    for key, value in given["header"]:
        if key.lower() in ("authorization", "cookie"):
            print("gh-forge: dropping %s: it would hand your credential to a third "
                  "party, and the forge uses its own accounts" % key, file=sys.stderr)
            continue
        headers[key] = value
    if given["preview"]:
        previews = ",".join(given["preview"])
        headers["Accept"] = "application/vnd.github.%s-preview+json" % previews

    endpoint, inline_query = _split_endpoint(endpoint)
    params = urllib.parse.parse_qsl(inline_query, keep_blank_values=True)

    body = None
    is_graphql = endpoint.strip("/") == "graphql"

    if method != "GET":
        if not is_graphql:
            raise Unsupported(
                "the read-only forge only serves GET /github/<path>; "
                "a %s cannot be proxied (it needs your own authenticated gh)" % method)

    if method == "GET":
        if given["raw-field"] or given["field"]:
            params.extend(given["raw-field"])
            params.extend(given["field"])

    if is_graphql:
        if method != "POST":
            method = "POST"
        fields = dict(given["raw-field"] + given["field"])
        payload = {}
        if "query" not in fields:
            raise UsageError("a GraphQL call needs -f query='...'")
        payload["query"] = fields["query"]
        if fields.get("variables"):
            try:
                payload["variables"] = json.loads(fields["variables"])
            except ValueError:
                raise UsageError("variables must be JSON")
        if fields.get("operationName"):
            payload["operationName"] = fields["operationName"]
        if opts.get("input"):
            with open(opts["input"], "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()

    if repoctx.needs_placeholders(endpoint):
        repo = ctx.repo()
        if not repo:
            raise UsageError("the endpoint uses {owner}/{repo} but no repository was found")
        endpoint = repoctx.expand(endpoint, repo)

    client = ctx.backend.client
    accept = headers.get("Accept")
    cache_ttl = httpmod.Cache.parse_duration(opts.get("cache")) if opts.get("cache") else None
    cache = httpmod.Cache() if cache_ttl else None

    if opts.get("paginate") and method == "GET" and not is_graphql:
        # gh filters each page on its own when --jq or --template is given
        # (two pages of 10 and 7 print 10 then 7), and only merges pages into
        # one array when it prints them itself.
        filtering = opts.get("jq") is not None or opts.get("template") is not None
        pages = []
        for page in ctx.backend.pages(endpoint, params, headers):
            if not isinstance(page, (list, dict)):
                break
            if filtering and not opts.get("slurp"):
                _emit(opts, page)
                continue
            pages.append(page)
            if opts.get("slurp"):
                continue
        if filtering and not opts.get("slurp"):
            return 0
        if opts.get("slurp"):
            _emit(opts, pages)
            return 0
        merged, kind = _merge(pages)
        if kind == "array":
            _emit(opts, merged)
        else:
            for page in pages:
                _emit(opts, page)
        return 0

    url = client.base + "/github/" + endpoint.lstrip("/") + httpmod._encode(params)
    resp = cache.get(url, accept) if cache else None
    if resp is None:
        resp = client.request(method, url, headers=headers, body=body)
        if cache:
            cache.put(url, accept, resp, cache_ttl)

    if opts.get("verbose"):
        print("* Request: %s %s" % (method, url), file=sys.stderr)
        for key, value in sorted(headers.items()):
            print("> %s: %s" % (key, value), file=sys.stderr)

    _raise_for_status(resp)

    if opts.get("silent"):
        return 0
    output.print_response(resp,
                          jq_expr=opts.get("jq"),
                          tpl=opts.get("template"),
                          include=bool(opts.get("include")))
    return 0


def _split_endpoint(endpoint):
    endpoint = endpoint.strip()
    for prefix in ("https://api.github.com/", "http://api.github.com/",
                   "https://github.com/api/v3/"):
        if endpoint.startswith(prefix):
            endpoint = endpoint[len(prefix):]
            break
    if endpoint.startswith("/"):
        endpoint = endpoint[1:]
    if "?" in endpoint:
        path, _, query = endpoint.partition("?")
        return path, query
    return endpoint, ""


def _merge(pages):
    lists = [page for page in pages if isinstance(page, list)]
    if len(lists) == len(pages) and lists:
        merged = []
        for page in lists:
            merged.extend(page)
        return merged, "array"
    if all(isinstance(page, dict) for page in pages) and len(pages) == 1:
        return pages[0], "object"
    return pages, "pages"


def _emit(opts, data):
    if opts.get("jq") is not None:
        output.write_raw(output.jq(opts["jq"], data))
    elif opts.get("template") is not None:
        output.write_raw(output.template(opts["template"], data))
    else:
        output.write(output.dumps(data))


def _raise_for_status(resp):
    if resp.status < 400:
        return
    problem = resp.problem()
    if problem:
        message = problem.get("detail") or problem.get("title") or "request failed"
        hint = problem.get("hint")
        reason = problem.get("reason")
        if reason == "own-account":
            hint = hint or "use /users/<login> for a public account's reads"
        elif reason in ("none-configured", "permanently-exhausted"):
            hint = hint or "this read needs a keyed forge account; use a REST endpoint instead"
        elif reason in ("held", "rate-limited", "disabled"):
            hint = hint or "retry later"
        raise GhError("%s (HTTP %d)" % (message, resp.status), hint=hint,
                      body=resp.body, status=resp.status)
    # GitHub's own error: gh prints the body, then the message.
    body = resp.body.decode("utf-8", "replace")
    if body:
        # gh writes the body through untouched; it does not add a newline.
        output.write_raw(body)
    message = None
    try:
        doc = json.loads(body)
        if isinstance(doc, dict):
            message = doc.get("message")
    except ValueError:
        pass
    raise GhError("%s (HTTP %d)" % (message or "request failed", resp.status),
                  body=resp.body, status=resp.status)
