"""Which gh answers: the real one, or the forge proxy.

Order of preference is the point of the tool:

    real  -> the gh on PATH, with the caller's own token, whenever it is
             installed and its token still works
    forge -> the read-only proxy, when there is no gh, no token, an expired or
             revoked token, a suspended account, or the API is exhausted

Mode is `auto` unless GH_FORGE_MODE is `real` or `forge`. The probe result is
cached for a few minutes so an agent's loop does not pay for it on every call;
a failure of the real gh at runtime still drops through to the forge.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

from . import http
from .errors import GhError

CACHE_TTL = 300

# stderr shapes that mean "this token cannot be used", not "this request was
# wrong". Kept narrow on purpose: a 404 or a 422 must not trigger a fallback
# that answers something else.
_BAN_MARKERS = (
    "bad credentials",
    "http 401",
    "requires authentication",
    "token has expired",
    "token is invalid",
    "has been suspended",
    "account suspended",
    "flagged",
    "saml enforcement",
    "you must authenticate",
    "gh auth login",
    "rate limit",
)


def real_gh_path():
    """The gh on PATH that is not this wrapper."""
    override = os.environ.get("GH_FORGE_REAL")
    if override:
        return override if os.path.exists(override) else None
    me = os.path.realpath(sys.argv[0]) if sys.argv else ""
    here = os.path.dirname(os.path.abspath(__file__))
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        if not entry:
            continue
        candidate = os.path.join(entry, "gh")
        if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            continue
        resolved = os.path.realpath(candidate)
        if resolved == me:
            continue
        if resolved.startswith(os.path.dirname(here)):
            continue
        return candidate
    return None


def _cache_file():
    return os.path.join(http.cache_dir(), "mode.json")


def _gh_config_stamp():
    """A cheap fingerprint of the gh config so a re-login invalidates cache."""
    home = os.environ.get("GH_CONFIG_DIR") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "gh")
    for name in ("hosts.yml", "config.yml"):
        path = os.path.join(home, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        return "%s:%d:%d" % (name, stat.st_mtime_ns, stat.st_size)
    return "none"


def _read_cache():
    try:
        with open(_cache_file(), "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if doc.get("expires", 0) < time.time():
        return None
    if doc.get("stamp") != _gh_config_stamp():
        return None
    return doc


def _write_cache(mode, reason):
    path = _cache_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"mode": mode, "reason": reason,
                       "stamp": _gh_config_stamp(),
                       "expires": time.time() + CACHE_TTL}, handle)
        os.replace(tmp, path)
    except OSError:
        pass


def _probe_real(gh):
    """Run the two cheap local/remote checks the real gh offers."""
    try:
        proc = subprocess.run([gh, "auth", "status", "--hostname", "github.com"],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "auth probe failed: %s" % exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "") + (proc.stdout or "")
        return False, "not authenticated: " + _first_line(stderr)
    # `auth status` does not validate the token. One API call does, and it
    # also tells us whether the account is still allowed to make requests.
    try:
        proc = subprocess.run([gh, "api", "rate_limit"],
                              capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError) as exc:
        return True, "auth probe incomplete: %s" % exc
    if proc.returncode != 0:
        blob = ((proc.stderr or "") + (proc.stdout or "")).lower()
        if any(marker in blob for marker in _BAN_MARKERS):
            return False, "token unusable: " + _first_line(proc.stderr or proc.stdout)
        return False, "rate_limit probe failed: " + _first_line(proc.stderr or proc.stdout)
    return True, "authenticated"


def decision(force_refresh=False):
    """Return ('real'|'forge', reason, gh_path)."""
    forced = (os.environ.get("GH_FORGE_MODE") or "auto").strip().lower()
    gh = real_gh_path()

    if forced == "forge":
        return "forge", "GH_FORGE_MODE=forge", gh
    if forced == "real":
        if not gh:
            raise GhError("GH_FORGE_MODE=real but no gh on PATH")
        return "real", "GH_FORGE_MODE=real", gh

    if not gh:
        return "forge", "no gh on PATH", None

    cached = None if force_refresh else _read_cache()
    if cached:
        return cached["mode"], "cached: " + cached.get("reason", ""), gh

    ok, reason = _probe_real(gh)
    mode = "real" if ok else "forge"
    _write_cache(mode, reason)
    return mode, reason, gh


def note_fallback(reason):
    """Remember a decision taken at runtime, so the next call goes straight on."""
    _write_cache("forge", reason)


def looks_like_auth_failure(stderr):
    blob = (stderr or "").lower()
    return any(marker in blob for marker in _BAN_MARKERS)


def _first_line(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return "no detail"


def run_real(gh, argv, fallback_ok=True):
    """Exec the real gh with the caller's stdio, exactly as if it were gh.

    stdout is inherited so paging, colour and TTY detection stay the real
    thing. stderr is captured only to recognise a dead token afterwards.
    """
    try:
        proc = subprocess.run([gh] + list(argv), stderr=subprocess.PIPE)
    except OSError as exc:
        raise GhError("could not run %s: %s" % (gh, exc))
    err = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
    if proc.returncode == 0:
        return 0
    if fallback_ok and looks_like_auth_failure(err):
        note_fallback("runtime: " + _first_line(err))
        return None
    if err:
        sys.stderr.write(err)
    return proc.returncode


def token_in_env():
    return bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))


def describe():
    mode, reason, gh = decision()
    return {"mode": mode, "reason": reason, "gh": gh}


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:12]
