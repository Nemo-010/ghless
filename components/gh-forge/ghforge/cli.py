"""The `gh` entry point.

argv is handled in this order: the real gh takes it whenever a working
authenticated gh exists; otherwise the command is answered from the forge
proxy. `gh auth status` is the one command that describes the switch instead
of hiding it, because that is where a person looks for it.
"""

from __future__ import annotations

import os
import sys

from . import modes, output
from .errors import GhError, Unsupported, UsageError

VERSION = None  # filled in from the package


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        return _root_help()

    if argv[0] in ("--version", "version"):
        return _version(argv)

    # `help` is gh's alias for --help.
    if argv[0] in ("-h", "--help", "help"):
        if len(argv) > 1:
            return _help_for(argv[1:])
        return _root_help()

    try:
        mode, reason, gh = modes.decision()
    except GhError as exc:
        return _fail(exc)

    if os.environ.get("GH_FORGE_DEBUG"):
        sys.stderr.write("gh-forge: mode=%s (%s)\n" % (mode, reason))

    if mode == "real" and gh:
        # Hand the command to the real gh untouched: same argv, same stdio.
        code = modes.run_real(gh, argv)
        if code is not None:
            return code
        # The token died mid-flight: answer the same command from the forge.
        sys.stderr.write("gh-forge: the authenticated gh cannot answer; "
                         "falling back to the read-only forge\n")

    return _dispatch(argv)


def _dispatch(argv):
    ctx = Context()
    try:
        return _route(argv, ctx)
    except UsageError as exc:
        # gh prints its own usage errors bare, with no "gh:" prefix.
        sys.stderr.write("%s\n" % exc.message)
        return exc.exit_code
    except Unsupported as exc:
        sys.stderr.write("gh: %s\n" % exc.message)
        return exc.exit_code
    except GhError as exc:
        return _fail(exc)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:
        return 130


class Context:
    """What a command module gets: a backend, a repo resolver, a person cache."""

    def __init__(self, backend=None):
        from .backend import Backend
        self.backend = backend or Backend(debug=bool(os.environ.get("GH_FORGE_DEBUG")))
        self.debug = bool(os.environ.get("GH_FORGE_DEBUG"))
        self._repo = None
        self._names = {}
        self._name_budget = 40
        import threading
        self._lock = threading.Lock()

    def repo(self, explicit=None, required=True):
        from . import repoctx
        if explicit:
            return repoctx.parse(explicit)
        if self._repo is not None:
            return self._repo
        self._repo = repoctx.resolve(None, required=required)
        return self._repo

    def resolve_names(self, logins):
        """Look up display names once per login; missing names stay empty."""
        found = {}
        todo = [l for l in dict.fromkeys(logins) if l]
        # Independent lookups run together; the request budget is still honoured
        # (display_name spends it under a lock) and the answers do not change.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            for login, name in zip(todo, pool.map(self.display_name, todo)):
                found[login] = name
        return found

    def display_name(self, login):
        from .errors import GhError
        with self._lock:
            if login in self._names:
                return self._names[login]
            if self._name_budget <= 0:
                return ""
            self._name_budget -= 1
        name = ""
        try:
            doc = self.backend.data("users/%s" % login)
            if isinstance(doc, dict):
                name = doc.get("name") or ""
        except GhError:
            name = ""
        self._names[login] = name
        return name


def _route(argv, ctx):
    from .cmds import actions, api, issue, pr, repo
    try:
        from .cmds import search
    except ImportError:
        search = None

    command = argv[0]
    rest = argv[1:]

    if command == "api":
        return api.run(rest, ctx)
    if command == "repo":
        return _repo_route(rest, ctx)
    if command == "issue":
        return _issue_route(rest, ctx)
    if command == "pr":
        return _pr_route(rest, ctx)
    if command == "release":
        return _release_route(rest, ctx)
    if command in ("run", "workflow", "label"):
        return _actions_route(command, rest, ctx)
    if command == "search" and search is not None:
        return search.run(rest, ctx)
    if command == "auth":
        return _auth_route(rest)
    if command == "gist" and rest and rest[0] == "view":
        from .cmds import transfer
        return transfer.gist_view(rest[1:], ctx)
    if command in ("config", "extension", "gist", "project", "codespace",
                   "copilot", "ssh-key", "gpg-key", "alias", "secret", "variable"):
        raise Unsupported(
            "%r is not part of the read-only forge; only reads of repositories, "
            "issues, pull requests, releases, actions and search are proxied" % command)
    raise UsageError('unknown command "%s" for "gh"' % command)


def _repo_route(args, ctx):
    from .cmds import repo
    if not args:
        raise UsageError('specify a subcommand: "gh repo view" or "gh repo list"')
    sub, rest = args[0], args[1:]
    if sub == "view":
        return repo.view(rest, ctx)
    if sub == "list":
        return repo.list_repos(rest, ctx)
    if sub == "clone":
        from .cmds import transfer
        return transfer.repo_clone(rest, ctx)
    raise Unsupported("gh repo %s writes; the read-only forge serves reads only" % sub)


def _issue_route(args, ctx):
    from .cmds import issue
    if not args:
        raise UsageError('specify a subcommand: "gh issue list" or "gh issue view"')
    sub, rest = args[0], args[1:]
    if sub == "list":
        return issue.list_issues(rest, ctx)
    if sub == "view":
        return issue.view_issue(rest, ctx)
    raise Unsupported("gh issue %s writes; the read-only forge serves reads only" % sub)


def _pr_route(args, ctx):
    from .cmds import pr
    if not args:
        raise UsageError('specify a subcommand: "gh pr list", "gh pr view" or "gh pr diff"')
    sub, rest = args[0], args[1:]
    if sub == "list":
        return pr.list_prs(rest, ctx)
    if sub == "view":
        return pr.view_pr(rest, ctx)
    if sub == "diff":
        return pr.diff_pr(rest, ctx)
    if sub == "checks":
        return _pr_checks(rest, ctx)
    raise Unsupported("gh pr %s writes; the read-only forge serves reads only" % sub)


def _pr_checks(args, ctx):
    from . import output, shape
    from .cmds import common
    from .cmds.pr import _checks
    from .flags import Spec, split
    spec = Spec()
    spec.value("json")
    spec.value("jq", "q")
    spec.value("template", "t")
    opts, rest = split(args, spec)
    if not rest:
        raise UsageError("accepts 1 arg(s), received 0")
    number = common.require_int(rest[0])
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    doc = ctx.backend.data("repos/%s/%s/pulls/%d" % (owner, name, number))
    rollup = _checks(ctx, owner, name, doc) or []
    output.write(output.dumps(rollup))
    return 0


def _release_route(args, ctx):
    from .cmds import actions
    if not args:
        raise UsageError('specify a subcommand: "gh release list" or "gh release view"')
    sub, rest = args[0], args[1:]
    if sub == "list":
        return actions.release_list(rest, ctx)
    if sub == "view":
        return actions.release_view(rest, ctx)
    if sub == "download":
        from .cmds import transfer
        return transfer.release_download(rest, ctx)
    raise Unsupported("gh release %s writes; the read-only forge serves reads only" % sub)


def _actions_route(command, args, ctx):
    from .cmds import actions
    if command == "label":
        return actions.label_list(args, ctx)
    if not args:
        raise UsageError('specify a subcommand, e.g. "gh %s list"' % command)
    sub, rest = args[0], args[1:]
    if command == "run":
        if sub == "list":
            return actions.run_list(rest, ctx)
        if sub == "view":
            return actions.run_view(rest, ctx)
        raise Unsupported("gh run %s writes; the read-only forge serves reads only" % sub)
    if command == "workflow":
        if sub == "list":
            return actions.workflow_list(rest, ctx)
        if sub == "view":
            return _workflow_view(rest, ctx)
        raise Unsupported("gh workflow %s writes; the read-only forge serves reads only" % sub)
    raise UsageError('unknown command "%s"' % command)


def _workflow_view(args, ctx):
    from . import output, shape
    from .cmds import common
    from .flags import Spec, split
    spec = Spec()
    spec.value("json")
    spec.value("jq", "q")
    spec.value("template", "t")
    spec.flag("web", "w")
    opts, rest = split(args, spec)
    if not rest:
        raise UsageError("accepts 1 arg(s), received 0")
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/", 1)
    selector = rest[0]
    doc = ctx.backend.data("repos/%s/%s/actions/workflows" % (owner, name),
                           [("per_page", "100")])
    found = None
    for item in (doc or {}).get("workflows") or []:
        if str(item.get("id")) == selector or item.get("name") == selector \
                or item.get("path") == selector:
            found = item
            break
    if not found:
        from .errors import UsageError as UE
        raise UE("workflow not found: %s" % selector)
    shaped = shape.workflow(found)
    if opts.get("json") or opts.get("jq") or opts.get("template"):
        common.emit(shaped, opts, "workflow list")
        return 0
    output.write("%s - %s" % (shaped["name"], shaped["path"]))
    output.write("ID: %s" % shaped["id"])
    return 0


def _auth_route(args):
    from . import http, modes
    sub = args[0] if args else "status"
    if sub == "status":
        mode, reason, gh = modes.decision()
        if mode == "real":
            return modes.run_real(gh, ["auth", "status"] + args[1:]) or 0
        print("github.com")
        print("  x Not logged in to any GitHub hosts. Run gh auth login to authenticate.")
        print()
        print("  Falls back to the read-only forge at %s" % http.base_url())
        print("  (%s). Reads of public repositories, issues and pull requests work;" % reason)
        print("  writes and viewer-specific reads do not.")
        return 1
    if sub == "token":
        raise Unsupported("there is no token: the forge service uses its own accounts "
                          "and never shares a credential")
    if sub == "login":
        raise Unsupported("logging in needs a browser and a writable credential store; "
                          "run the real gh for that")
    output.write("usage: gh auth status")
    return 0


def _fail(exc):
    prefix = "" if getattr(exc, "bare", False) else "gh: "
    sys.stderr.write("%s%s\n" % (prefix, exc.message))
    if getattr(exc, "hint", None):
        sys.stderr.write("%s%s\n" % (prefix, exc.hint))
    return getattr(exc, "exit_code", 1)


def _version(argv):
    from . import __version__
    if os.environ.get("GH_FORGE_MODE") == "real":
        raise SystemExit(0)
    try:
        mode, reason, gh = modes.decision()
    except GhError:
        mode, reason, gh = "forge", "unknown", None
    if mode == "real" and gh:
        code = modes.run_real(gh, ["--version"])
        if code == 0:
            print("gh-forge %s (via %s)" % (__version__, gh))
            return 0
    # Keep the first line shaped like gh's own, so version checks keep working,
    # and say plainly which layer answered.
    print("gh version %s (gh-forge %s, read-only forge)" % (_real_version(gh), __version__))
    print("https://forge.web.pkgforge.dev")
    return 0


def _real_version(gh):
    if not gh:
        return "0.0.0"
    try:
        import subprocess
        proc = subprocess.run([gh, "--version"], capture_output=True, text=True, timeout=10)
    except Exception:
        return "0.0.0"
    first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
    parts = first.split()
    if len(parts) >= 3 and parts[0] == "gh" and parts[1] == "version":
        return parts[2]
    return "0.0.0"


def _root_help():
    output.write("""Work seamlessly with GitHub from the command line.

USAGE
  gh <command> <subcommand> [flags]

CORE COMMANDS
  api:        Make an authenticated GitHub API request
  issue:      Manage issues
  pr:         Manage pull requests
  release:    Manage releases
  repo:       Create, clone, fork, and view repositories
  run:        View details about workflow runs
  search:     Search for repositories, issues, and pull requests
  workflow:   View details about GitHub Actions workflows
  label:      Manage labels

Available in read-only forge mode (no authenticated gh):
  repo view, repo list, issue list, issue view, pr list, pr view, pr diff,
  release list, release view, run list, run view, workflow list, workflow view,
  label list, search repos|issues|prs|code|commits, api (GET)

ENVIRONMENT
  GH_FORGE_MODE   auto (default) | real | forge
  GH_FORGE_BASE   proxy base URL (default https://forge.web.pkgforge.dev)
  GH_FORGE_DEBUG  set to trace mode decisions and requests
""")
    return 0


def _help_for(args):
    from .cmds import api, actions, issue, pr, repo
    topic = " ".join(args[:2])
    table = {
        "api": api.USAGE,
        "repo view": repo.VIEW_USAGE,
        "repo list": repo.LIST_USAGE,
        "issue list": issue.LIST_USAGE,
        "issue view": issue.VIEW_USAGE,
        "pr list": pr.LIST_USAGE,
        "pr view": pr.VIEW_USAGE,
        "release list": actions.RELEASE_LIST_USAGE,
        "release view": actions.RELEASE_VIEW_USAGE,
        "run list": actions.RUN_LIST_USAGE,
    }
    text = table.get(topic) or table.get(args[0])
    if text:
        output.write(text)
        return 0
    return _root_help()


if __name__ == "__main__":
    sys.exit(main())
