"""`gh pr list`, `gh pr view` and `gh pr diff`."""

from __future__ import annotations

from .. import fields, output, shape
from ..errors import GhError, UsageError
from ..flags import Spec, split
from . import common

LIST_USAGE = """gh pr list [flags]

Flags:
  -a, --assignee string   Filter by assignee
  -A, --author string     Filter by author
  -B, --base string       Filter by base branch
  -d, --draft             Filter by draft state
  -H, --head string       Filter by head branch
  -l, --label strings     Filter by label
  -L, --limit int         Maximum number of items to fetch (default 30)
  -S, --search query      Search pull requests with query
  -s, --state string      Filter by state: {open|closed|merged|all} (default "open")
"""

VIEW_USAGE = """gh pr view <number> [flags]

Flags:
  -c, --comments          View pull request comments
  -q, --jq expression     Filter JSON output using a jq expression
      --json fields       Output JSON with the specified fields
  -t, --template string   Format JSON output using a Go template
"""


def _list_spec():
    s = Spec()
    s.value("assignee", "a")
    s.value("author", "A")
    s.value("base", "B")
    s.flag("draft", "d")
    s.value("head", "H")
    s.value("label", "l")
    s.value("limit", "L")
    s.value("search", "S")
    s.value("state", "s")
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


def _text_state(item):
    if item.get("mergedAt") or item.get("merged_at"):
        return "MERGED"
    return (item.get("state") or "").upper()


def list_prs(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
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
    fields.check(requested, "pr list")

    state = (opts.get("state") or "open").lower()
    heavy = [f for f in requested if f in
             ("statusCheckRollup", "files", "reviews", "reviews", "latestReviews",
              "comments", "commits", "reviewRequests", "mergeCommit")]

    if opts.get("search"):
        query = "repo:%s is:pr %s" % (repo, opts["search"])
        if opts.get("author"):
            query += " author:" + opts["author"]
        result = ctx.backend.search("search/issues", query, limit=limit,
                                    sort="created", order="desc")
        numbers = [item.get("number") for item in (result or {}).get("items") or []]
        items = []
        for number in numbers:
            items.append(ctx.backend.data("repos/%s/%s/pulls/%d" % (owner, name, number)))
    else:
        params = [("state", "all" if state in ("all", "merged") else state),
                  ("sort", "created"), ("direction", "desc")]
        wanted_labels = common.parse_csv(opts.get("label"))

        def keep(item):
            if state == "merged" and not (item.get("merged_at") or item.get("merged")):
                return False
            if opts.get("base") and item.get("base", {}).get("ref") != opts["base"]:
                return False
            if opts.get("head") and item.get("head", {}).get("ref") != opts["head"]:
                return False
            if opts.get("author") and (item.get("user") or {}).get("login") != opts["author"]:
                return False
            if opts.get("assignee"):
                logins = [(u or {}).get("login") for u in item.get("assignees") or []]
                if opts["assignee"] not in logins:
                    return False
            if opts.get("draft") and not item.get("draft"):
                return False
            labels = [l.get("name") for l in item.get("labels") or []]
            if wanted_labels and not all(label in labels for label in wanted_labels):
                return False
            return True

        items = ctx.backend.collect("repos/%s/%s/pulls" % (owner, name), params,
                                    limit=limit, keep=keep)
        if not isinstance(items, list):
            items = []

    names = ctx.resolve_names(
        [login for item in items for login in shape.logins_in(item)])
    shaped = []
    for item in items:
        extras = {}
        if heavy and state != "merged":
            number = item.get("number")
            base = "repos/%s/%s/pulls/%d" % (owner, name, number)
            if "statusCheckRollup" in requested:
                extras["checks"] = _checks(ctx, owner, name, item)
            if "files" in requested:
                files = ctx.backend.list(base + "/files")
                extras["files"] = [shape.pr_file(f) for f in (files or [])]
            if "reviews" in requested or "latestReviews" in requested:
                reviews = ctx.backend.list(base + "/reviews")
                extras["reviews"] = [shape.review(r) for r in (reviews or [])]
        shaped.append(shape.pr(item, names=names, **extras))

    if common.json_supported(opts):
        common.emit(shaped, opts, "pr list")
        return 0

    rows = []
    for item in shaped:
        rows.append([item["number"], item["title"], item["headRefName"],
                     item["state"], item["updatedAt"]])
    output.write_raw(output.table(rows))
    return 0


def _checks(ctx, owner, name, pull):
    sha = (pull.get("head") or {}).get("sha")
    if not sha:
        return None
    runs = []
    try:
        doc = ctx.backend.data("repos/%s/%s/commits/%s/check-runs" % (owner, name, sha),
                               [("per_page", "100")])
        runs = (doc or {}).get("check_runs") or []
    except GhError:
        runs = []
    statuses = []
    try:
        doc = ctx.backend.data("repos/%s/%s/commits/%s/status" % (owner, name, sha))
        statuses = (doc or {}).get("statuses") or []
    except GhError:
        statuses = []
    return shape.check_rollup(pull, runs, statuses)


def view_pr(argv, ctx):
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
    fields.check(requested, "pr view")

    base = "repos/%s/%s/pulls/%d" % (owner, name, number)
    doc = ctx.backend.data(base)
    extras = {}
    if "files" in requested:
        extras["files"] = [shape.pr_file(f) for f in
                           (ctx.backend.list(base + "/files") or [])]
    if "statusCheckRollup" in requested:
        extras["checks"] = _checks(ctx, owner, name, doc)
    if "reviews" in requested or "latestReviews" in requested or "reviewDecision" in requested:
        extras["reviews"] = [shape.review(r) for r in
                             (ctx.backend.list(base + "/reviews") or [])]
    if "reviewRequests" in requested:
        extras["review_requests"] = [
            shape.review_request(r) for r in
            (ctx.backend.list(base + "/requested_reviewers") or {})]
    if "comments" in requested or opts.get("comments"):
        raw = ctx.backend.list("repos/%s/%s/issues/%d/comments" % (owner, name, number))
        extras["comments"] = [shape.issue_comment(c) for c in (raw or [])]
    if "commits" in requested:
        raw = ctx.backend.list(base + "/commits")
        extras["commits"] = [shape.commit_node(c) for c in (raw or [])]

    shaped = shape.pr(doc, names=ctx.resolve_names(shape.logins_in(doc)), **extras)

    if common.json_supported(opts):
        common.emit(shaped, opts, "pr view")
        return 0

    reviewers = []
    for review in shaped.get("reviews") or []:
        login = (review.get("author") or {}).get("login")
        state = (review.get("state") or "").title()
        if login:
            reviewers.append("%s (%s)" % (login, state.replace("_", " ")))
    lines = [
        ("title", shaped["title"]),
        ("state", shaped["state"]),
        ("author", common.person(ctx, (doc or {}).get("user"))),
        ("labels", ", ".join(l["name"] for l in shaped["labels"] or [])),
        ("assignees", common.person_list(ctx, (doc or {}).get("assignees"))),
        ("reviewers", ", ".join(dict.fromkeys(reviewers))),
        ("projects", ""),
        ("milestone", (shaped["milestone"] or {}).get("title") or ""),
        ("number", shaped["number"]),
        ("url", shaped["url"]),
        ("additions", shaped["additions"]),
        ("deletions", shaped["deletions"]),
        ("auto-merge", "enabled" if shaped["autoMergeRequest"] else "disabled"),
    ]
    for key, value in lines:
        output.write("%s:\t%s" % (key, "" if value is None else value))
    output.write("--")
    body = shaped["body"] or ""
    output.write_raw(body if body.endswith("\n") or not body else body + "\n")
    for comment in extras.get("comments") or []:
        author = (comment.get("author") or {}).get("login") or ""
        output.write_raw("\n%s\t%s\n" % (author, comment.get("createdAt") or ""))
        text = comment.get("body") or ""
        output.write_raw(text if text.endswith("\n") else text + "\n")
    return 0


def diff_pr(argv, ctx):
    spec = Spec()
    spec.value("repo", "R")
    spec.flag("name-only")
    spec.flag("patch")
    spec.value("name-only")  # tolerate --name-only=false
    opts, args = split(argv, spec)
    if not args:
        raise UsageError("accepts 1 arg(s), received 0")
    number = common.require_int(args[0])
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    accept = ("application/vnd.github.v3.diff" if not opts.get("patch")
              else "application/vnd.github.v3.patch")
    resp = ctx.backend.response("repos/%s/%s/pulls/%d" % (owner, name, number),
                                headers={"Accept": accept})
    if resp.status >= 400:
        ctx.backend._decode(resp)
    output.write_raw(resp.text())
    return 0
