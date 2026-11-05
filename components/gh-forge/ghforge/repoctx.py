"""Which repository a command is talking about.

gh resolves it from `-R/--repo`, then GH_REPO, then the git remote of the
current directory. `gh api` additionally expands `{owner}`, `{repo}` and
`{branch}` in the endpoint, so that has to work here too.
"""

from __future__ import annotations

import os
import re
import subprocess

from .errors import UsageError

_REMOTE_PATTERNS = (
    re.compile(r"^(?:https?|ssh)://(?:[^@/]+@)?github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
)


def parse(text):
    """'[HOST/]OWNER/REPO' -> 'OWNER/REPO' (the proxy only does github.com)."""
    value = (text or "").strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    parts = value.split("/")
    if len(parts) == 3:
        host, owner, name = parts
        if host not in ("github.com", "api.github.com", "www.github.com"):
            raise UsageError("unknown host %r: the forge service reads github.com only" % host)
        value = owner + "/" + name
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise UsageError('expected the "[HOST/]OWNER/REPO" format, got %r' % text)
    return parts[0] + "/" + parts[1]


def _git(*args):
    try:
        proc = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def from_git_remote():
    url = _git("remote", "get-url", "origin")
    candidates = [url] if url else []
    if not candidates:
        listing = _git("remote")
        for name in (listing or "").split():
            candidates.append(_git("remote", "get-url", name) or "")
    for candidate in candidates:
        for pattern in _REMOTE_PATTERNS:
            match = pattern.match(candidate or "")
            if match:
                return match.group("owner") + "/" + match.group("repo")
    return None


def resolve(explicit=None, *, required=True):
    """The repository for this command, or None when it does not matter."""
    value = explicit or os.environ.get("GH_REPO")
    if value:
        return parse(value)
    found = from_git_remote()
    if found:
        return found
    if required:
        raise UsageError(
            "no git remotes found: run this command inside a repository, "
            "or use -R/--repo OWNER/REPO")
    return None


def expand(endpoint, repo):
    """Expand gh api's {owner} and {repo} placeholders."""
    if "{" not in endpoint:
        return endpoint
    owner, name = repo.split("/", 1)
    branch = None
    for key, value in (("{owner}", owner), ("{repo}", name)):
        endpoint = endpoint.replace(key, value)
    if "{branch}" in endpoint:
        import subprocess as _sp
        try:
            proc = _sp.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
            branch = proc.stdout.strip() if proc.returncode == 0 else "HEAD"
        except OSError:
            branch = "HEAD"
        endpoint = endpoint.replace("{branch}", branch or "HEAD")
    return endpoint


def needs_placeholders(endpoint):
    return any(token in endpoint for token in ("{owner}", "{repo}", "{branch}"))
