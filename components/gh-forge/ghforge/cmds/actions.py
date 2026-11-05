"""`gh release list`, `gh release view`, `gh workflow list`, `gh label list`,
`gh run list` and `gh run view`."""

from __future__ import annotations

from .. import fields, output, shape
from ..errors import GhError, UsageError
from ..flags import Spec, split
from . import common

RELEASE_LIST_USAGE = """gh release list [flags]

Flags:
      --exclude-drafts         Exclude draft releases
      --exclude-pre-releases   Exclude pre-releases
  -L, --limit int              Maximum number of items to fetch (default 30)
  -O, --order string           Order of releases returned: {asc|desc} (default "desc")
"""

RELEASE_VIEW_USAGE = """gh release view [<tag>] [flags]

Flags:
  -q, --jq expression     Filter JSON output using a jq expression
      --json fields       Output JSON with the specified fields
  -t, --template string   Format JSON output using a Go template
"""

RUN_LIST_USAGE = """gh run list [flags]

Flags:
  -b, --branch string     Filter runs by branch
  -c, --commit SHA        Filter runs by the SHA of the commit
      --created date      Filter runs by the date it was created
  -e, --event event       Filter runs by which event triggered the run
  -L, --limit int         Maximum number of runs to fetch (default 20)
  -s, --status string     Filter runs by status
  -u, --user string       Filter runs by user who triggered the run
  -w, --workflow string   Filter runs by workflow
"""


def _release_list_spec():
    s = Spec()
    s.flag("exclude-drafts")
    s.flag("exclude-pre-releases")
    s.value("limit", "L")
    s.value("order", "O")
    s.value("repo", "R")
    s.value("json")
    s.value("jq", "q")
    s.value("template", "t")
    return s


def _release_view_spec():
    s = Spec()
    s.value("repo", "R")
    s.value("json")
    s.value("jq", "q")
    s.value("template", "t")
    s.flag("web", "w")
    return s


def release_list(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(RELEASE_LIST_USAGE)
        return 0
    opts, args = split(argv, _release_list_spec())
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    limit = int(opts.get("limit") or 30)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "release list")

    params = [("per_page", "100"),
              ("direction", "asc" if (opts.get("order") or "desc") == "asc" else "desc")]
    items = ctx.backend.collect("repos/%s/%s/releases" % (owner, name), params,
                                limit=limit,
                                keep=lambda item: not (
                                    (opts.get("exclude-drafts") and item.get("draft"))
                                    or (opts.get("exclude-pre-releases")
                                        and item.get("prerelease"))))
    if not isinstance(items, list):
        items = []
    latest_tag = _latest_tag(ctx, owner, name)
    selected = []
    for item in items:
        if opts.get("exclude-drafts") and item.get("draft"):
            continue
        if opts.get("exclude-pre-releases") and item.get("prerelease"):
            continue
        selected.append(shape.release(item, is_latest=(item.get("tag_name") == latest_tag)))
        if len(selected) >= limit:
            break

    if common.json_supported(opts):
        common.emit(selected, opts, "release list")
        return 0

    rows = []
    for item in selected:
        tag = ""
        if item["isDraft"]:
            tag = "Draft"
        elif item["isLatest"] and not item["isPrerelease"]:
            tag = "Latest"
        elif item["isPrerelease"]:
            tag = "Pre-release"
        rows.append([item["name"], tag, item["tagName"], item["publishedAt"]])
    output.write_raw(output.table(rows))
    return 0


def _latest_tag(ctx, owner, name):
    try:
        doc = ctx.backend.data("repos/%s/%s/releases/latest" % (owner, name))
    except GhError:
        return None
    return (doc or {}).get("tag_name")


def _release_doc(ctx, owner, name, tag):
    if not tag or tag in ("latest", "--latest"):
        return ctx.backend.data("repos/%s/%s/releases/latest" % (owner, name))
    return ctx.backend.data("repos/%s/%s/releases/tags/%s" % (owner, name, tag))


def release_view(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(RELEASE_VIEW_USAGE)
        return 0
    opts, args = split(argv, _release_view_spec())
    if opts.get("web"):
        raise UsageError("--web needs a browser and a GitHub session; "
                         "the read-only forge cannot open one")
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    tag = args[0] if args else None
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "release view")

    doc = _release_doc(ctx, owner, name, tag)
    latest_tag = _latest_tag(ctx, owner, name)
    shaped = shape.release_detail(doc, is_latest=(doc.get("tag_name") == latest_tag))

    if common.json_supported(opts):
        common.emit(shaped, opts, "release view")
        return 0

    output.write("title:\t%s" % (shaped["name"] or shaped["tagName"]))
    output.write("tag:\t%s" % shaped["tagName"])
    output.write("draft:\t%s" % ("true" if shaped["isDraft"] else "false"))
    output.write("prerelease:\t%s" % ("true" if shaped["isPrerelease"] else "false"))
    output.write("author:\t%s" % common.person(ctx, (doc or {}).get("author")))
    output.write("created:\t%s" % shaped["createdAt"])
    output.write("published:\t%s" % shaped["publishedAt"])
    output.write("url:\t%s" % shaped["url"])
    if shaped["assets"]:
        output.write("assets:")
        for asset in shaped["assets"]:
            output.write("  %s\t%s" % (asset.get("name"), asset.get("url")))
    body = shaped["body"] or ""
    if body:
        output.write_raw(body if body.endswith("\n") else body + "\n")
    return 0


# -- actions -------------------------------------------------------------

def _run_list_spec():
    s = Spec()
    s.flag("all", "a")
    s.value("branch", "b")
    s.value("commit", "c")
    s.value("created")
    s.value("event", "e")
    s.value("limit", "L")
    s.value("status", "s")
    s.value("user", "u")
    s.value("workflow", "w")
    s.value("repo", "R")
    s.value("json")
    s.value("jq", "q")
    s.value("template", "t")
    return s


def run_list(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(RUN_LIST_USAGE)
        return 0
    opts, args = split(argv, _run_list_spec())
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    limit = int(opts.get("limit") or 20)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "run list")

    params = []
    if opts.get("branch"):
        params.append(("branch", opts["branch"]))
    if opts.get("event"):
        params.append(("event", opts["event"]))
    if opts.get("status"):
        params.append(("status", opts["status"]))
    if opts.get("user"):
        params.append(("actor", opts["user"]))
    if opts.get("commit"):
        params.append(("head_sha", opts["commit"]))
    if opts.get("created"):
        params.append(("created", opts["created"]))
    params.append(("per_page", "100"))

    if opts.get("workflow"):
        wf = _workflow_id(ctx, owner, name, opts["workflow"])
        path = "repos/%s/%s/actions/workflows/%s/runs" % (owner, name, wf)
        doc = ctx.backend.data(path, params)
        items = (doc or {}).get("workflow_runs") or []
    else:
        doc = ctx.backend.data("repos/%s/%s/actions/runs" % (owner, name), params)
        items = (doc or {}).get("workflow_runs") or []

    items = items[:limit]
    shaped = [shape.run(item) for item in items]

    if common.json_supported(opts):
        common.emit(shaped, opts, "run list")
        return 0

    rows = []
    for item in shaped:
        rows.append([item["status"], item["conclusion"], item["displayTitle"],
                     item["workflowName"], item["headBranch"], item["event"],
                     item["databaseId"],
                     common.duration_text(item["createdAt"], item["updatedAt"]),
                     item["createdAt"]])
    output.write_raw(output.table(rows))
    return 0


def _workflow_id(ctx, owner, name, selector):
    if selector.isdigit():
        return selector
    doc = ctx.backend.data("repos/%s/%s/actions/workflows" % (owner, name))
    for item in (doc or {}).get("workflows") or []:
        if item.get("path") == selector or item.get("name") == selector \
                or (item.get("path") or "").endswith("/" + selector):
            return str(item.get("id"))
    raise UsageError('no workflow found for %r' % selector)


def _run_view_spec():
    s = Spec()
    s.value("repo", "R")
    s.value("json")
    s.value("jq", "q")
    s.value("template", "t")
    s.flag("log")
    s.flag("log-failed")
    s.value("job")
    s.flag("web", "w")
    return s


def run_view(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write("gh run view [<run-id>] [flags]\n")
        return 0
    opts, args = split(argv, _run_view_spec())
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "run view")

    run_id = args[0] if args else None
    if run_id is None and opts.get("job"):
        run_id = _run_of_job(ctx, owner, name, opts["job"])
    if run_id is None:
        raise UsageError("accepts 1 arg(s), received 0")
    if not str(run_id).isdigit():
        raise UsageError("invalid run id: %r" % run_id)

    doc = ctx.backend.data("repos/%s/%s/actions/runs/%s" % (owner, name, run_id))
    jobs = None
    if opts.get("log") or opts.get("log-failed"):
        return _print_log(ctx, owner, name, run_id, failed=bool(opts.get("log-failed")))
    if "jobs" in requested or not common.json_supported(opts):
        raw = ctx.backend.data("repos/%s/%s/actions/runs/%s/jobs" % (owner, name, run_id),
                               [("per_page", "100")])
        jobs = [shape.job(j) for j in (raw or {}).get("jobs") or []]

    shaped = shape.run(doc, jobs=jobs)

    if common.json_supported(opts):
        common.emit(shaped, opts, "run view")
        return 0

    output.write("%s %s %s" % (_run_mark(shaped), shaped["displayTitle"],
                               shaped["headBranch"]))
    output.write("Triggered via %s on %s" % (shaped["event"], shaped["createdAt"]))
    output.write("JOBS")
    for job in jobs or []:
        output.write("%s %s (%s)" % (_run_mark(job), job.get("name"),
                                     common.duration_text(job.get("startedAt"),
                                                          job.get("completedAt"))))
        for step in job.get("steps") or []:
            output.write("  %s %s" % (_run_mark(step), step.get("name")))
    return 0


def _run_mark(item):
    conclusion = (item.get("conclusion") or "").upper()
    status = (item.get("status") or "").upper()
    if conclusion == "SUCCESS":
        return "✓"
    if conclusion in ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE"):
        return "X"
    if conclusion in ("CANCELLED", "SKIPPED", "NEUTRAL"):
        return "-"
    if status in ("IN_PROGRESS", "QUEUED"):
        return "*"
    return "-"


def _run_of_job(ctx, owner, name, job_id):
    doc = ctx.backend.data("repos/%s/%s/actions/runs" % (owner, name),
                           [("per_page", "1")])
    return None


def _print_log(ctx, owner, name, run_id, failed=False):
    doc = ctx.backend.data("repos/%s/%s/actions/runs/%s/jobs" % (owner, name, run_id),
                           [("per_page", "100")])
    for job in (doc or {}).get("jobs") or []:
        if failed and job.get("conclusion") == "success":
            continue
        try:
            resp = ctx.backend.response(
                "repos/%s/%s/actions/jobs/%s/logs" % (owner, name, job.get("id")))
        except GhError:
            continue
        if resp.status == 200:
            output.write_raw(resp.text())
    return 0


# -- workflows and labels ------------------------------------------------

def workflow_list(argv, ctx):
    spec = Spec()
    spec.flag("all", "a")
    spec.value("limit", "L")
    spec.value("repo", "R")
    spec.value("json")
    spec.value("jq", "q")
    spec.value("template", "t")
    opts, args = split(argv, spec)
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    limit = int(opts.get("limit") or 50)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "workflow list")

    doc = ctx.backend.data("repos/%s/%s/actions/workflows" % (owner, name),
                           [("per_page", "100")])
    items = (doc or {}).get("workflows") or []
    if opts.get("all"):
        try:
            disabled = ctx.backend.data(
                "repos/%s/%s/actions/workflows" % (owner, name),
                [("per_page", "100")])
        except GhError:
            disabled = None
    items = items[:limit]
    shaped = [shape.workflow(item) for item in items]

    if common.json_supported(opts):
        common.emit(shaped, opts, "workflow list")
        return 0
    rows = [[item["name"], item["state"], item["id"]] for item in shaped]
    output.write_raw(output.table(rows))
    return 0


def label_list(argv, ctx):
    spec = Spec()
    spec.value("repo", "R")
    spec.value("json")
    spec.value("jq", "q")
    spec.value("template", "t")
    spec.value("limit", "L")
    spec.value("order")
    spec.value("search", "S")
    spec.value("sort")
    opts, args = split(argv, spec)
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    limit = int(opts.get("limit") or 30)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "label list")

    sort = opts.get("sort") or "created"
    params = [("per_page", "100"), ("sort", sort),
              ("direction", opts.get("order") or "asc")]
    needle = (opts.get("search") or "").lower()
    items = ctx.backend.collect(
        "repos/%s/%s/labels" % (owner, name), params, limit=limit,
        keep=(lambda item: needle in (item.get("name") or "").lower()
              or needle in (item.get("description") or "").lower()) if needle else None)
    if not isinstance(items, list):
        items = []
    items = items[:limit]
    shaped = [shape.label_row(item) for item in items]

    if common.json_supported(opts):
        common.emit(shaped, opts, "label list")
        return 0
    rows = [[item["name"], item["description"],
             "#" + (item["color"] or "")] for item in shaped]
    output.write_raw(output.table(rows))
    return 0
