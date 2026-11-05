"""`gh issue list` and `gh issue view`."""

from __future__ import annotations

from .. import fields, output, shape
from ..errors import Unsupported, UsageError
from ..flags import Spec, split
from . import common

LIST_USAGE = """gh issue list [flags]

Flags:
  -a, --assignee string    Filter by assignee
  -A, --author string      Filter by author
  -q, --jq expression      Filter JSON output using a jq expression
      --json fields        Output JSON with the specified fields
  -l, --label strings      Filter by label
  -L, --limit int          Maximum number of issues to fetch (default 30)
  -m, --milestone string   Filter by milestone number or title
  -S, --search query       Search issues with query
  -s, --state string       Filter by state: {open|closed|all} (default "open")
  -t, --template string    Format JSON output using a Go template
"""

VIEW_USAGE = """gh issue view <number> [flags]

Flags:
  -c, --comments          View issue comments
  -q, --jq expression     Filter JSON output using a jq expression
      --json fields       Output JSON with the specified fields
  -t, --template string   Format JSON output using a Go template
"""


def _list_spec():
    s = Spec()
    s.value("assignee", "a")
    s.value("author", "A")
    s.value("label", "l")
    s.value("limit", "L")
    s.value("mention")
    s.value("milestone", "m")
    s.value("search", "S")
    s.value("state", "s")
    s.value("type")
    s.value("repo", "R")
    s.value("json")
    s.value("jq", "q")
    s.value("template", "t")
    s.flag("web", "w")
    return s


def _view_spec():
    s = Spec()
    s.value("repo", "R")
    s.flag("comments", "c")
    s.value("json")
    s.value("jq", "q")
    s.value("template", "t")
    s.flag("web", "w")
    return s


def _comments(ctx, owner, name, number):
    items = ctx.backend.list("repos/%s/%s/issues/%s/comments" % (owner, name, number))
    if not isinstance(items, list):
        return []
    return [shape.issue_comment(item) for item in items]


def _maybe_comments(ctx, owner, name, items, requested, flagged):
    """Only pay for comment bodies when somebody actually asked for them."""
    if not flagged and "comments" not in (requested or []):
        return None
    out = {}
    for item in items:
        if item.get("pull_request") and not item.get("number"):
            continue
        out[item.get("number")] = _comments(ctx, owner, name, item.get("number"))
    return out


def _query(opts, repo):
    """gh issue list's flags as a search query, when it has to search."""
    parts = []
    state = (opts.get("state") or "open").lower()
    if state != "all":
        parts.append("state:" + state)
    if opts.get("author"):
        parts.append("author:" + opts["author"])
    if opts.get("assignee"):
        parts.append("assignee:" + opts["assignee"])
    if opts.get("mention"):
        parts.append("mentions:" + opts["mention"])
    for label in common.parse_csv(opts.get("label")):
        parts.append('label:"%s"' % label if " " in label else "label:" + label)
    if opts.get("milestone"):
        milestone = opts["milestone"]
        parts.append("milestone:" + milestone)
    if opts.get("search"):
        parts.append(opts["search"])
    return " ".join(parts)


def list_issues(argv, ctx):
    if argv and argv[0] in ("-h", "--help", "help"):
        output.write(LIST_USAGE)
        return 0
    opts, args = split(argv, _list_spec())
    if opts.get("web"):
        raise UsageError("--web needs a browser and a GitHub session; "
                         "the read-only forge cannot open one")
    if args:
        raise UsageError("accepts 0 arg(s), received %d" % len(args))
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    limit = int(opts.get("limit") or 30)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "issue list")

    search = opts.get("search") or opts.get("mention") or opts.get("author") \
        or opts.get("assignee") or opts.get("label") or opts.get("milestone")

    if search:
        query = _query(opts, repo)
        query = ("repo:%s is:issue %s" % (repo, query)).strip()
        result = ctx.backend.search("search/issues", query, limit=limit,
                                    sort="created", order="desc")
        items = (result or {}).get("items") or []
    else:
        state = (opts.get("state") or "open").lower()
        params = [("state", "all" if state == "all" else state),
                  ("sort", "created"), ("direction", "desc")]
        items = ctx.backend.collect("repos/%s/%s/issues" % (owner, name), params,
                                    limit=limit,
                                    keep=lambda item: "pull_request" not in item)
        if not isinstance(items, list):
            items = []

    comments = _maybe_comments(ctx, owner, name, items, requested, False)
    names = ctx.resolve_names(
        [login for item in items for login in shape.logins_in(item)])
    shaped = []
    for item in items:
        shaped.append(shape.issue(
            item, comments=comments.get(item.get("number")) if comments else None,
            names=names))

    if common.json_supported(opts):
        common.emit(shaped, opts, "issue list")
        return 0

    rows = []
    for item in shaped:
        rows.append([item["number"], item["state"], item["title"],
                     ", ".join(l["name"] for l in item["labels"] or []),
                     item["updatedAt"]])
    output.write_raw(output.table(rows))
    return 0


def view_issue(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(VIEW_USAGE)
        return 0
    opts, args = split(argv, _view_spec())
    if opts.get("web"):
        raise UsageError("--web needs a browser and a GitHub session; "
                         "the read-only forge cannot open one")
    if not args:
        raise UsageError("accepts 1 arg(s), received 0")
    number = common.require_int(args[0])
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "issue view")

    doc = ctx.backend.data("repos/%s/%s/issues/%s" % (owner, name, number))
    comments = None
    if opts.get("comments") or "comments" in requested:
        comments = _comments(ctx, owner, name, number)
    names = ctx.resolve_names(shape.logins_in(doc))
    shaped = shape.issue(doc, comments=comments, names=names)

    if common.json_supported(opts):
        common.emit(shaped, opts, "issue view")
        return 0

    subject = (doc or {}).get("user") or {}
    lines = [
        ("title", shaped["title"]),
        ("state", shaped["state"]),
        ("author", common.person(ctx, subject)),
        ("labels", ", ".join(l["name"] for l in shaped["labels"] or [])),
        ("comments", str(doc.get("comments", 0))),
        ("assignees", common.person_list(ctx, (doc or {}).get("assignees"))),
        ("projects", ""),
        ("milestone", (shaped["milestone"] or {}).get("title") or ""),
        ("issue-type", (shaped["issueType"] or {}).get("name") or ""),
        ("parent", ""),
        ("sub-issues", ""),
        ("sub-issues-completed", ""),
        ("blocked-by", ""),
        ("blocking", ""),
        ("number", shaped["number"]),
    ]
    for key, value in lines:
        output.write("%s:\t%s" % (key, "" if value is None else value))
    output.write("--")
    body = shaped["body"] or ""
    output.write_raw(body if body.endswith("\n") or not body else body + "\n")
    for comment in comments or []:
        author = (comment.get("author") or {}).get("login") or ""
        output.write_raw("\n%s\t%s\n" % (author, comment.get("createdAt") or ""))
        text = comment.get("body") or ""
        output.write_raw(text if text.endswith("\n") else text + "\n")
    return 0
