"""`gh repo view` and `gh repo list`."""

from __future__ import annotations

from .. import output, shape
from ..errors import UsageError
from ..flags import Spec, split
from . import common

VIEW_USAGE = """gh repo view [<repository>] [flags]

Flags:
  -b, --branch string     View a specific branch of the repository
  -q, --jq expression     Filter JSON output using a jq expression
      --json fields       Output JSON with the specified fields
  -t, --template string   Format JSON output using a Go template
  -w, --web               Open a repository in the browser
"""

LIST_USAGE = """gh repo list [<owner>] [flags]

Flags:
  -q, --jq expression       Filter JSON output using a jq expression
      --json fields         Output JSON with the specified fields
  -L, --limit int           Maximum number of repositories to list (default 30)
  -t, --template string     Format JSON output using a Go template
"""


def _view_spec():
    s = Spec()
    s.value("branch", "b")
    s.value("json")
    s.value("jq", "q")
    s.value("template", "t")
    s.flag("web", "w")
    return s


def _list_spec():
    s = Spec()
    s.value("json")
    s.value("jq", "q")
    s.value("template", "t")
    s.value("limit", "L")
    s.value("language", "l")
    s.list("topic")
    s.value("visibility")
    s.flag("archived")
    s.flag("no-archived")
    s.flag("fork")
    s.flag("source")
    s.flag("web", "w")
    return s


def view(argv, ctx):
    if not argv or argv[0] in ("-h", "--help"):
        if argv:
            output.write(VIEW_USAGE)
            return 0
    opts, args = split(argv, _view_spec())
    if opts.get("web"):
        raise UsageError("--web needs a browser and a GitHub session; "
                         "the read-only forge cannot open one")
    name = args[0] if args else None
    repo = common.repo_for(ctx, opts, positional=name)
    owner, short = repo.split("/", 1)

    doc = ctx.backend.data("repos/%s/%s" % (owner, short))
    from .. import fields
    requested = fields.parse(opts.get("json")) if opts.get("json") else []

    languages = None
    if "languages" in requested:
        table = ctx.backend.data("repos/%s/%s/languages" % (owner, short))
        if isinstance(table, dict):
            languages = sorted(table.items(), key=lambda item: item[1], reverse=True)

    latest = None
    if "latestRelease" in requested:
        try:
            latest = ctx.backend.data("repos/%s/%s/releases/latest" % (owner, short))
        except Exception:
            latest = None

    shaped = shape.repo(doc, languages=languages,
                        latest_release=shape.release(latest) if latest else None)

    if common.json_supported(opts):
        common.emit(shaped, opts, "repo view")
        return 0

    branch = opts.get("branch")
    readme = None
    try:
        readme = ctx.backend.response(
            "repos/%s/%s/readme" % (owner, short),
            [("ref", branch)] if branch else None,
            {"Accept": "application/vnd.github.raw"})
    except Exception:
        readme = None
    output.write("name:\t%s" % shaped["nameWithOwner"])
    output.write("description:\t%s" % shaped["description"])
    if readme is not None and readme.status == 200 and readme.body:
        output.write("--")
        output.write_raw(readme.text())
        # gh terminates the readme with a newline even when raw lacks one
        if not readme.text().endswith("\n"):
            output.write("")
        else:
            output.write("")
    return 0


def list_repos(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(LIST_USAGE)
        return 0
    opts, args = split(argv, _list_spec())
    if opts.get("web"):
        raise UsageError("--web needs a browser and a GitHub session; "
                         "the read-only forge cannot open one")
    limit = int(opts.get("limit") or 30)
    owner = args[0] if args else None
    if not owner:
        repo = ctx.repo(None, required=False)
        if repo:
            owner = repo.split("/", 1)[0]
        else:
            owner = None
    if not owner:
        raise UsageError("expected an OWNER argument, or run inside a checkout")

    doc = ctx.backend.data("users/%s" % owner)
    kind = (doc or {}).get("type")
    base = "orgs/%s/repos" % owner if kind == "Organization" else "users/%s/repos" % owner
    params = [("sort", "pushed"), ("direction", "desc"), ("type", "all")]
    items = ctx.backend.collect(base, params, limit=limit, keep=None)
    if not isinstance(items, list):
        items = []

    language = opts.get("language")
    topics = set(common.parse_csv(opts.get("topic")))
    visibility = (opts.get("visibility") or "").lower()

    selected = []
    for item in items:
        if opts.get("fork") and not item.get("fork"):
            continue
        if opts.get("source") and item.get("fork"):
            continue
        if opts.get("archived") and not item.get("archived"):
            continue
        if opts.get("no-archived") and item.get("archived"):
            continue
        if language and (item.get("language") or "").lower() != language.lower():
            continue
        if topics and not topics.issubset(set(item.get("topics") or [])):
            continue
        if visibility and (item.get("visibility") or "").lower() != visibility:
            continue
        selected.append(shape.repo(item))
        if len(selected) >= limit:
            break

    if common.json_supported(opts):
        common.emit(selected, opts, "repo list")
        return 0
    rows = []
    for item in selected:
        visibility_text = (item.get("visibility") or "").lower()
        if item.get("isArchived"):
            visibility_text += ", archived"
        rows.append([item.get("nameWithOwner"), item.get("description"),
                     visibility_text, item.get("pushedAt")])
    output.write_raw(output.table(rows))
    return 0
