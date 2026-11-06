"""`gh search repos|issues|prs|code|commits`.

gh builds search output from GraphQL; the proxy serves the REST search
endpoints, so the flags become query qualifiers and the REST items are mapped
into gh's JSON shapes. Two measured quirks this reproduces:

    - `gh search code` prints one line per text-match line: "repo:path: fragment"
      (the fragments arrive only with the text-match media type)
    - `author`/`assignee`/`owner` objects carry type and url here, unlike the
      issue-list shapes, and `repository` differs per subcommand
"""

from __future__ import annotations

from .. import fields, output, shape
from ..errors import Unsupported, UsageError
from ..flags import Spec, split
from . import common

TEXT_MATCH_ACCEPT = "application/vnd.github.text-match+json"

REPOS_USAGE = """gh search repos [<query>] [flags]

Flags:
      --archived                    Filter based on the repository archived state {true|false}
      --created date                Filter based on created at date
      --followers number            Filter based on number of followers
      --forks number                Filter on number of forks
      --good-first-issues number    Filter on number of issues with the 'good first issue' label
      --help-wanted-issues number   Filter on number of issues with the 'help wanted' label
      --include-forks string        Include forks in fetched repositories: {false|true|only}
  -q, --jq expression               Filter JSON output using a jq expression
      --json fields                 Output JSON with the specified fields
      --language string             Filter based on the coding language
      --license strings             Filter based on license type
  -L, --limit int                   Maximum number of repositories to fetch (default 30)
      --match strings               Restrict search to specific field of repository: {name|description|readme}
      --number-topics number        Filter on number of topics
      --order string                Order of repositories returned, ignored unless '--sort' flag is specified: {asc|desc} (default "desc")
      --owner strings               Filter on owner
      --size string                 Filter on a size range, in kilobytes
      --sort string                 Sort fetched repositories: {forks|help-wanted-issues|stars|updated} (default "best-match")
      --stars number                Filter on number of stars
  -t, --template string             Format JSON output using a Go template; see "gh help formatting"
      --topic strings               Filter on topic
      --updated date                Filter on last updated at date
      --visibility strings          Filter based on visibility: {public|private|internal}
  -w, --web                         Open the search query in the web browser
"""

ISSUES_USAGE = """gh search issues [<query>] [flags]

Flags:
      --app string             Filter by GitHub App author
      --archived               Filter based on the repository archived state {true|false}
      --assignee string        Filter by assignee
      --author string          Filter by author
      --closed date            Filter on closed at date
      --commenter user         Filter based on comments by user
      --comments number        Filter on number of comments
      --created date           Filter based on created at date
      --include-prs            Include pull requests in results
      --interactions number    Filter on number of reactions and comments
      --involves user          Filter based on involvement of user
  -q, --jq expression          Filter JSON output using a jq expression
      --json fields            Output JSON with the specified fields
      --label strings          Filter on label
      --language string        Filter based on the coding language
  -L, --limit int              Maximum number of results to fetch (default 30)
      --locked                 Filter on locked conversation status
      --match strings          Restrict search to specific field of issue: {title|body|comments}
      --mentions user          Filter based on user mentions
      --milestone title        Filter by milestone title
      --no-assignee            Filter on missing assignee
      --no-label               Filter on missing label
      --no-milestone           Filter on missing milestone
      --no-project             Filter on missing project
      --order string           Order of results returned, ignored unless '--sort' flag is specified: {asc|desc} (default "desc")
      --owner strings          Filter on repository owner
      --reactions number       Filter on number of reactions
  -R, --repo OWNER/REPO        Filter on repository, in OWNER/REPO format
      --sort string            Sort fetched results: {comments|created|interactions|reactions|reactions-+1|reactions--1|reactions-heart|reactions-smile|reactions-tada|reactions-thinking_face|updated} (default "best-match")
      --state string           Filter based on state: {open|closed}
  -t, --template string        Format JSON output using a Go template; see "gh help formatting"
      --updated date           Filter on last updated at date
      --visibility strings     Filter based on repository visibility: {public|private|internal}
  -w, --web                    Open the search query in the web browser
"""

PRS_USAGE = ISSUES_USAGE.replace("gh search issues", "gh search prs")

CODE_USAGE = """gh search code [<query>] [flags]

Flags:
      --extension string   Filter on file extension
      --filename string    Filter on filename
  -q, --jq expression      Filter JSON output using a jq expression
      --json fields        Output JSON with the specified fields
      --language string    Filter results by language
  -L, --limit int          Maximum number of code results to fetch (default 30)
      --match strings      Restrict search to file contents or file path: {file|path}
      --owner strings      Filter on owner
  -R, --repo OWNER/REPO    Filter on repository, in OWNER/REPO format
      --size string        Filter on size range, in kilobytes
  -t, --template string    Format JSON output using a Go template; see "gh help formatting"
  -w, --web                Open the search query in the web browser
"""

COMMITS_USAGE = """gh search commits [<query>] [flags]

Flags:
      --author string            Filter by author
      --author-date date         Filter based on authored date
      --author-email string      Filter on author email
      --author-name string       Filter on author name
      --committer string         Filter by committer
      --committer-date date      Filter based on committed date
      --committer-email string   Filter on committer email
      --committer-name string    Filter on committer name
      --hash string              Filter by commit hash
  -q, --jq expression            Filter JSON output using a jq expression
      --json fields              Output JSON with the specified fields
  -L, --limit int                Maximum number of commits to fetch (default 30)
      --merge                    Filter on merge commits
      --order string             Order of commits returned, ignored unless '--sort' flag is specified: {asc|desc} (default "desc")
      --owner strings            Filter on repository owner
      --parent string            Filter by parent hash
  -R, --repo OWNER/REPO          Filter on repository, in OWNER/REPO format
      --sort string              Sort fetched commits: {author-date|committer-date} (default "best-match")
  -t, --template string          Format JSON output using a Go template; see "gh help formatting"
      --tree string              Filter by tree hash
      --visibility strings       Filter based on repository visibility: {public|private|internal}
  -w, --web                      Open the search query in the web browser
"""


def _base_spec():
    spec = Spec()
    spec.value("json")
    spec.value("jq", "q")
    spec.value("template", "t")
    spec.value("limit", "L")
    spec.value("order")
    spec.flag("web", "w")
    return spec


def _repos_spec():
    spec = _base_spec()
    spec.flag("archived")
    spec.value("created")
    spec.value("followers")
    spec.value("forks")
    spec.value("good-first-issues")
    spec.value("help-wanted-issues")
    spec.value("include-forks")
    spec.value("language")
    spec.list("license")
    spec.list("match")
    spec.value("number-topics")
    spec.list("owner")
    spec.value("size")
    spec.value("sort")
    spec.value("stars")
    spec.list("topic")
    spec.value("updated")
    spec.list("visibility")
    return spec


def _issues_spec():
    spec = _base_spec()
    spec.value("repo", "R")
    spec.value("app")
    spec.flag("archived")
    spec.value("assignee")
    spec.value("author")
    spec.value("closed")
    spec.value("commenter")
    spec.value("comments")
    spec.value("created")
    spec.flag("include-prs")
    spec.value("interactions")
    spec.value("involves")
    spec.list("label")
    spec.value("language")
    spec.flag("locked")
    spec.list("match")
    spec.value("mentions")
    spec.value("milestone")
    spec.flag("no-assignee")
    spec.flag("no-label")
    spec.flag("no-milestone")
    spec.flag("no-project")
    spec.list("owner")
    spec.value("reactions")
    spec.value("sort")
    spec.value("state")
    spec.list("visibility")
    return spec


def _code_spec():
    spec = _base_spec()
    spec.value("repo", "R")
    spec.value("extension")
    spec.value("filename")
    spec.value("language")
    spec.list("match")
    spec.list("owner")
    spec.value("size")
    return spec


def _commits_spec():
    spec = _base_spec()
    spec.value("repo", "R")
    spec.value("author")
    spec.value("author-date")
    spec.value("author-email")
    spec.value("author-name")
    spec.value("committer")
    spec.value("committer-date")
    spec.value("committer-email")
    spec.value("committer-name")
    spec.value("hash")
    spec.flag("merge")
    spec.list("owner")
    spec.value("parent")
    spec.value("sort")
    spec.list("visibility")
    spec.value("tree")
    return spec


# ---------------------------------------------------------------- qualifiers

def _join(parts):
    return " ".join(part for part in parts if part)


def _date_flag(opts, name, qualifier):
    if opts.get(name):
        return "%s:%s" % (qualifier, opts[name])
    return None


def _repos_query(opts, words):
    parts = []
    for owner in common.parse_csv(opts.get("owner")):
        parts.append("user:%s" % owner)
    if opts.get("archived"):
        parts.append("archived:true")
    parts.append(_date_flag(opts, "created", "created"))
    for flag, qualifier in (("followers", "followers"), ("forks", "forks"),
                            ("good-first-issues", "good-first-issues"),
                            ("help-wanted-issues", "help-wanted-issues"),
                            ("number-topics", "topics"), ("stars", "stars")):
        if opts.get(flag):
            value = str(opts[flag])
            if value[:1] in "<>":
                parts.append("%s:%s" % (qualifier, value))
            else:
                parts.append("%s:>=%s" % (qualifier, value))
    forks = opts.get("include-forks")
    if forks in ("true", "false", "only"):
        parts.append("fork:%s" % forks)
    if opts.get("language"):
        parts.append("language:%s" % opts["language"])
    for licence in common.parse_csv(opts.get("license")):
        parts.append("license:%s" % licence)
    matches = common.parse_csv(opts.get("match"))
    if matches:
        parts.append("in:%s" % ",".join(matches))
    if opts.get("size"):
        parts.append("size:%s" % opts["size"])
    for topic in common.parse_csv(opts.get("topic")):
        parts.append("topic:%s" % topic)
    for visibility in common.parse_csv(opts.get("visibility")):
        if visibility in ("public", "private", "internal"):
            parts.append("is:%s" % visibility)
    parts.append(_date_flag(opts, "updated", "updated"))
    return _join(parts + words)


_REPO_SORTS = {"forks": "forks", "stars": "stars", "updated": "updated"}


def _issues_query(opts, words, kind):
    if kind == "pr" or not opts.get("include-prs"):
        parts = ["type:%s" % kind]
    else:
        parts = []
    if opts.get("repo"):
        parts.append("repo:%s" % opts["repo"])
    for owner in common.parse_csv(opts.get("owner")):
        parts.append("user:%s" % owner)
    for flag, qualifier in (("author", "author"), ("assignee", "assignee"),
                            ("mentions", "mentions"), ("commenter", "commenter"),
                            ("involves", "involves"), ("milestone", "milestone")):
        if opts.get(flag):
            parts.append("%s:%s" % (qualifier, opts[flag]))
    for label in common.parse_csv(opts.get("label")):
        if " " in label:
            parts.append('label:"%s"' % label)
        else:
            parts.append("label:%s" % label)
    for flag in ("no-assignee", "no-label", "no-milestone", "no-project"):
        if opts.get(flag):
            parts.append("no:%s" % flag[3:])
    state = opts.get("state")
    if state in ("open", "closed"):
        parts.append("state:%s" % state)
    if opts.get("locked"):
        parts.append("locked:true")
    if opts.get("archived"):
        parts.append("archived:true")
    for flag, qualifier in (("comments", "comments"), ("interactions", "interactions"),
                            ("reactions", "reactions")):
        if opts.get(flag):
            parts.append("%s:>=%s" % (qualifier, opts[flag]))
    for name, qualifier in (("created", "created"), ("updated", "updated"),
                            ("closed", "closed")):
        parts.append(_date_flag(opts, name, qualifier))
    if opts.get("language"):
        parts.append("language:%s" % opts["language"])
    matches = common.parse_csv(opts.get("match"))
    if matches:
        parts.append("in:%s" % ",".join(matches))
    for visibility in common.parse_csv(opts.get("visibility")):
        if visibility in ("public", "private", "internal"):
            parts.append("is:%s" % visibility)
    if kind == "pr":
        for flag, qualifier in (("base", "base"), ("head", "head"),
                                ("review", "review"), ("reviewed-by", "reviewed-by"),
                                ("review-requested", "review-requested"),
                                ("app", "app")):
            if opts.get(flag):
                parts.append("%s:%s" % (qualifier, opts[flag]))
        if opts.get("draft"):
            parts.append("draft:true")
        if opts.get("merged"):
            parts.append("is:merged")
    return _join(parts + words)


_ISSUE_SORTS = {"created": "created", "updated": "updated", "comments": "comments",
                "reactions": "reactions"}


def _code_query(opts, words):
    parts = []
    for owner in common.parse_csv(opts.get("owner")):
        parts.append("user:%s" % owner)
    if opts.get("repo"):
        parts.append("repo:%s" % opts["repo"])
    if opts.get("language"):
        parts.append("language:%s" % opts["language"])
    if opts.get("size"):
        parts.append("size:%s" % opts["size"])
    if opts.get("filename"):
        parts.append("filename:%s" % opts["filename"])
    if opts.get("extension"):
        parts.append("extension:%s" % opts["extension"])
    matches = common.parse_csv(opts.get("match"))
    if matches:
        parts.append("in:%s" % ",".join(matches))
    return _join(parts + words)


def _commits_query(opts, words):
    parts = []
    for owner in common.parse_csv(opts.get("owner")):
        parts.append("user:%s" % owner)
    if opts.get("repo"):
        parts.append("repo:%s" % opts["repo"])
    for flag, qualifier in (("author", "author"), ("committer", "committer"),
                            ("hash", "hash"), ("parent", "parent"),
                            ("tree", "tree"), ("author-name", "author-name"),
                            ("author-email", "author-email"),
                            ("committer-name", "committer-name"),
                            ("committer-email", "committer-email")):
        if opts.get(flag):
            parts.append("%s:%s" % (qualifier, opts[flag]))
    for name, qualifier in (("author-date", "author-date"),
                            ("committer-date", "committer-date")):
        parts.append(_date_flag(opts, name, qualifier))
    if opts.get("merge"):
        parts.append("merge:true")
    for visibility in common.parse_csv(opts.get("visibility")):
        if visibility in ("public", "private", "internal"):
            parts.append("is:%s" % visibility)
    return _join(parts + words)


# ---------------------------------------------------------------- shapes

def _git_date(text):
    """REST git dates carry ".000" milliseconds; gh's GraphQL dates do not."""
    if isinstance(text, str) and "." in text:
        head, _, tail = text.partition(".")
        if "+" in tail or tail.endswith("Z"):
            return head + ("+" + tail.split("+", 1)[1] if "+" in tail else "Z")
    return text


def _actor(doc):
    """Search actors carry type and url, unlike the issue-list shapes."""
    if not doc:
        return None
    login = doc.get("login") or ""
    return {
        "id": doc.get("node_id") or doc.get("id"),
        "is_bot": bool(doc.get("type") == "Bot" or login.endswith("[bot]")),
        "login": login,
        "type": doc.get("type"),
        "url": doc.get("html_url"),
    }


def _repo_stub_for_issues(doc):
    """REST /search/issues items carry only repository_url; the shape gh prints
    (name, nameWithOwner) is rebuilt from it."""
    if isinstance(doc, dict) and doc.get("full_name"):
        return {"name": doc.get("name"), "nameWithOwner": doc.get("full_name")}
    return None


def _repo_from_url(url):
    marker = "/repos/"
    if not url or marker not in url:
        return None
    full = url.split(marker, 1)[1].split("?")[0].rstrip("/")
    parts = full.split("/")
    if len(parts) != 2:
        return None
    return {"name": parts[1], "nameWithOwner": full}


def _repo_stub_for_commits(doc):
    if not doc:
        return None
    return {
        "description": doc.get("description") or "",
        "fullName": doc.get("full_name"),
        "id": doc.get("node_id"),
        "isFork": bool(doc.get("fork")),
        "isPrivate": bool(doc.get("private")),
        "name": doc.get("name"),
        "owner": _actor(doc.get("owner")),
        "url": doc.get("html_url"),
    }


def _search_repo_item(doc):
    out = shape.search_repo(doc)
    out["owner"] = _actor(doc.get("owner"))
    return out


def _search_issue_item(doc, is_pr):
    out = shape.search_issue(doc, is_pr=is_pr)
    out["author"] = _actor(doc.get("user"))
    out["assignees"] = [_actor(u) for u in (doc.get("assignees") or [])]
    repository = doc.get("repository")
    if isinstance(repository, dict) and repository.get("full_name"):
        out["repository"] = {"name": repository.get("name"),
                             "nameWithOwner": repository.get("full_name")}
    else:
        out["repository"] = _repo_from_url(doc.get("repository_url"))
    return out


def _search_code_item(doc):
    return {
        "path": doc.get("path"),
        "repository": shape.repository_stub(doc.get("repository")),
        "sha": doc.get("sha"),
        "textMatches": [
            {
                "fragment": match.get("fragment"),
                "matches": [
                    {"indices": m.get("indices"), "text": m.get("text")}
                    for m in (match.get("matches") or [])
                ],
                "property": match.get("property"),
                "type": match.get("object_type"),
            }
            for match in (doc.get("text_matches") or [])
        ],
        "url": shape._get(doc, "repository", "html_url"),
    }


def _search_commit_item(doc):
    commit = doc.get("commit") or {}
    return {
        "author": _actor(doc.get("author")),
        "commit": {
            "author": {
                "date": _git_date(shape._get(commit, "author", "date")),
                "email": shape._get(commit, "author", "email"),
                "name": shape._get(commit, "author", "name"),
            },
            "comment_count": doc.get("comment_count", 0),
            "committer": {
                "date": _git_date(shape._get(commit, "committer", "date")),
                "email": shape._get(commit, "committer", "email"),
                "name": shape._get(commit, "committer", "name"),
            },
            "message": commit.get("message"),
            "tree": {"sha": shape._get(commit, "tree", "sha")},
        },
        "committer": _actor(doc.get("committer")),
        "id": doc.get("node_id"),
        "parents": [{"sha": p.get("sha")} for p in (doc.get("parents") or [])],
        "repository": _repo_stub_for_commits(doc.get("repository")),
        "sha": doc.get("sha"),
        "url": doc.get("html_url"),
    }


# ---------------------------------------------------------------- runners

def _emit(items, opts, command):
    if opts.get("jq") is not None and not opts.get("json"):
        raise UsageError("cannot use `--jq` without specifying `--json`")
    if opts.get("template") is not None and not opts.get("json"):
        raise UsageError("cannot use `--template` without specifying `--json`")
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    data = items
    if requested:
        data = [fields.select(item, requested, command) for item in items]
        data = common._sorted_deep(data)   # gh's search JSON is built from maps: fully sorted
    if opts.get("jq") is not None:
        output.write_raw(output.jq(opts["jq"], data))
    elif opts.get("template") is not None:
        output.write_raw(output.template(opts["template"], data))
    else:
        output.write(output.dumps(data))


def _limit_of(opts):
    limit = int(opts.get("limit") or 30)
    if limit > 1000:
        output.write("the provided limit %d is greater than the maximum of 1000" % limit,
                     )
        limit = 1000
    return limit


def _run_search(ctx, path, query, opts, *, sorts=None):
    limit = _limit_of(opts)
    sort = opts.get("sort")
    order = opts.get("order") or "desc"
    if not sort or sort == "best-match":
        sort_param = None
    else:
        sort_param = sorts.get(sort) if sorts else None
    return ctx.backend.search(path, query, sort=sort_param,
                              order=order if sort_param else None, limit=limit)


def search_repos(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(REPOS_USAGE)
        return 0
    opts, args = split(argv, _repos_spec())
    _no_web(opts)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "search repos")
    query = _repos_query(opts, args)
    if not query:
        raise UsageError("search text is required when using --owner or --repo")
    result = _run_search(ctx, "search/repositories", query, opts,
                         sorts=_REPO_SORTS)
    items = [_search_repo_item(item) for item in (result or {}).get("items") or []]
    if common.json_supported(opts):
        _emit(items, opts, "search repos")
        return 0
    rows = []
    for item in items:
        visibility = (item.get("visibility") or "").lower()
        if item.get("isArchived"):
            visibility += ", archived"
        rows.append([item.get("fullName"), item.get("description"),
                     visibility, item.get("updatedAt")])
    output.write_raw(output.table(rows))
    return 0


def search_issues(argv, ctx, kind="issue"):
    usage = PRS_USAGE if kind == "pr" else ISSUES_USAGE
    if argv and argv[0] in ("-h", "--help"):
        output.write(usage)
        return 0
    command = "search prs" if kind == "pr" else "search issues"
    opts, args = split(argv, _issues_spec())
    _no_web(opts)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, command)
    query = _issues_query(opts, args, kind)
    if not query:
        raise UsageError("search text is required when using --owner or --repo")
    result = _run_search(ctx, "search/issues", query, opts, sorts=_ISSUE_SORTS)
    items = [_search_issue_item(item, kind == "pr")
             for item in (result or {}).get("items") or []]
    if common.json_supported(opts):
        _emit(items, opts, command)
        return 0
    rows = []
    for item in items:
        repo = (item.get("repository") or {}).get("nameWithOwner") or ""
        labels = ", ".join(l.get("name") or "" for l in item.get("labels") or [])
        rows.append([repo, item.get("number"), item.get("state"),
                     item.get("title"), labels, item.get("updatedAt")])
    output.write_raw(output.table(rows))
    return 0


def search_prs(argv, ctx):
    return search_issues(argv, ctx, kind="pr")


def search_code(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(CODE_USAGE)
        return 0
    opts, args = split(argv, _code_spec())
    _no_web(opts)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "search code")
    query = _code_query(opts, args)
    if not query:
        raise UsageError("search text is required when using --owner or --repo")
    headers = {"Accept": TEXT_MATCH_ACCEPT}
    limit = _limit_of(opts)
    result = ctx.backend.search("search/code", query, limit=limit, headers=headers)
    items = [_search_code_item(item) for item in (result or {}).get("items") or []]
    if common.json_supported(opts):
        _emit(items, opts, "search code")
        return 0
    lines = []
    for item in items:
        repo = (item.get("repository") or {}).get("nameWithOwner") or ""
        path = item.get("path") or ""
        for match in item.get("textMatches") or []:
            fragment = (match.get("fragment") or "").strip()
            for line in fragment.split("\n"):
                if line.strip():
                    lines.append("%s:%s: %s" % (repo, path, line.strip()))
    if lines:
        output.write("\n".join(lines) + "\n")
    return 0


def search_commits(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(COMMITS_USAGE)
        return 0
    opts, args = split(argv, _commits_spec())
    _no_web(opts)
    requested = fields.parse(opts.get("json")) if opts.get("json") else []
    fields.check(requested, "search commits")
    query = _commits_query(opts, args)
    if not query.strip() or not args:
        raise UsageError('Invalid search query "%s".\n'
                         'Search text is required when searching commits. Searches that '
                         'use qualifiers only are not allowed. Were you searching for '
                         'something else?' % query)
    result = _run_search(ctx, "search/commits", query, opts,
                         sorts={"author-date": "author-date",
                                "committer-date": "committer-date"})
    items = [_search_commit_item(item) for item in (result or {}).get("items") or []]
    if common.json_supported(opts):
        _emit(items, opts, "search commits")
        return 0
    rows = []
    for item in items:
        repo = (item.get("repository") or {}).get("fullName") or ""
        message = ((item.get("commit") or {}).get("message") or "")
        rows.append([repo, item.get("sha"),
                     " ".join(message.split()),
                     (item.get("author") or {}).get("login") or "",
                     (item.get("commit") or {}).get("author", {}).get("date")])
    output.write_raw(output.table(rows))
    return 0


def _no_web(opts):
    if opts.get("web"):
        raise Unsupported("--web needs a browser and a GitHub session; "
                          "the read-only forge cannot open one")


def run(argv, ctx):
    if not argv:
        raise UsageError('specify a subcommand: "gh search repos", "gh search issues", '
                         '"gh search prs", "gh search code" or "gh search commits"')
    sub, rest = argv[0], argv[1:]
    if sub == "repos":
        return search_repos(rest, ctx)
    if sub == "issues":
        return search_issues(rest, ctx)
    if sub == "prs":
        return search_prs(rest, ctx)
    if sub == "code":
        return search_code(rest, ctx)
    if sub == "commits":
        return search_commits(rest, ctx)
    raise UsageError('unknown command "%s" for "gh search"' % sub)
