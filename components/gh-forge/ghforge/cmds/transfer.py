"""Byte transfers the read-only proxy cannot serve itself: `gh repo clone`,
`gh release download` and `gh gist view`.

Each tries the origin first and the fetch broker second, so a host that cannot
reach github.com directly still gets the bytes. Nothing here writes to GitHub.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from .. import output, repoctx
from ..errors import GhError, UsageError
from ..flags import Spec, split
from . import common

BROKER = os.environ.get("GH_FORGE_BROKER", "https://fetch.web.pkgforge.dev")

CLONE_USAGE = """gh repo clone <repository> [<directory>] [-- <gitflags>...]

Clones a public repository: github.com first, then the fetch broker, then the
source tarball (no history) when git cannot reach either.
"""

DOWNLOAD_USAGE = """gh release download [<tag>] [flags]

Flags:
  -A, --archive format   Download the source code archive in zip or tar.gz format
      --clobber          Overwrite existing files of the same name
  -D, --dir directory    The directory to download files into (default ".")
  -p, --pattern stringArray  Download only assets that match a glob pattern
  -O, --output file      The file to write a single asset to ("-" for stdout)
  -R, --repo [HOST/]OWNER/REPO
      --skip-existing    Skip downloading when files of the same name exist
"""

GIST_USAGE = """gh gist view [<id> | <url>] [flags]

Flags:
  -f, --filename string  Display a single file from the gist
      --raw              Do not try and render the gist
"""


def _git_env():
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def repo_clone(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(CLONE_USAGE)
        return 0
    gitflags = []
    if "--" in argv:
        cut = argv.index("--")
        argv, gitflags = argv[:cut], argv[cut + 1:]
    _, args = split(argv, Spec())
    if not args:
        raise UsageError("cannot clone: no repository name given")
    if len(args) > 2:
        raise UsageError("too many arguments")
    owner_repo = repoctx.parse(args[0].replace("https://github.com/", ""))
    name = owner_repo.split("/")[1]
    target = args[1] if len(args) == 2 else name
    url = "https://github.com/%s.git" % owner_repo
    if os.path.exists(target) and (not os.path.isdir(target) or os.listdir(target)):
        raise GhError("destination path %r already exists and is not an empty directory" % target)
    created = not os.path.exists(target)
    for source in (url, "%s/%s" % (BROKER, url)):
        proc = subprocess.run(["git", "clone"] + gitflags + ["--", source, target], env=_git_env())
        if proc.returncode == 0:
            subprocess.run(["git", "-C", target, "remote", "set-url", "origin", url],
                           capture_output=True)
            return 0
        if created:
            shutil.rmtree(target, ignore_errors=True)
        else:
            for leftover in os.listdir(target):
                path = os.path.join(target, leftover)
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    sys.stderr.write("gh: git routes failed; fetching the source tarball (no history)\n")
    tmp = tempfile.mkdtemp(prefix="gh-forge-clone.")
    try:
        archive = os.path.join(tmp, "src.tar.gz")
        _download(["https://codeload.github.com/%s/tar.gz/HEAD" % owner_repo], archive)
        subprocess.run(["tar", "-xzf", archive, "-C", tmp], check=True)
        top = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))][0]
        if not created:
            os.rmdir(target)
        shutil.move(os.path.join(tmp, top), target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    subprocess.run(["git", "-C", target, "init", "-q"], check=True)
    subprocess.run(["git", "-C", target, "add", "-A"], check=True)
    subprocess.run(["git", "-C", target, "-c", "user.name=gh-forge", "-c", "user.email=gh-forge@localhost",
                    "commit", "-q", "-m", "snapshot of %s (tarball, no history)" % owner_repo], check=True)
    subprocess.run(["git", "-C", target, "remote", "add", "origin", url], check=True)
    return 0


def _download(urls, dest, headers=None):
    """First URL that answers wins; each is also tried through the broker.
    A body shorter than its Content-Length is an error, never a saved file."""
    last = None
    part = dest + ".part"
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        for base in urls:
            for url in (base, "%s/%s" % (BROKER, base)):
                req = urllib.request.Request(url, headers=dict(headers or {}, **{"User-Agent": "gh-forge"}))
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp, open(part, "wb") as out:
                        want = resp.headers.get("Content-Length")
                        shutil.copyfileobj(resp, out)
                    if want is not None and os.path.getsize(part) != int(want):
                        raise OSError("short read: %d of %s bytes" % (os.path.getsize(part), want))
                    os.replace(part, dest)
                    return
                except (urllib.error.URLError, OSError) as exc:
                    last = exc
    finally:
        if os.path.exists(part):
            os.remove(part)
    raise GhError("download failed: %s" % last)


def _safe_name(name):
    base = os.path.basename(name or "")
    if not base or base in (".", "..") or "/" in name or "\\" in name or name != base:
        raise GhError("refusing asset name %r" % name)
    return base


def _release_download_spec():
    s = Spec()
    s.value("archive", "A")
    s.flag("clobber")
    s.value("dir", "D")
    s.list("pattern", "p")
    s.value("output", "O")
    s.value("repo", "R")
    s.flag("skip-existing")
    return s


def release_download(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(DOWNLOAD_USAGE)
        return 0
    opts, args = split(argv, _release_download_spec())
    repo = common.repo_for(ctx, opts)
    owner, name = repo.split("/")
    tag = args[0] if args else None
    if tag:
        doc = ctx.backend.data("repos/%s/%s/releases/tags/%s" % (owner, name, tag))
    else:
        doc = ctx.backend.data("repos/%s/%s/releases/latest" % (owner, name))
        tag = doc.get("tag_name")
    directory = opts.get("dir") or "."
    os.makedirs(directory, exist_ok=True)

    if opts.get("archive"):
        fmt = opts["archive"]
        if fmt not in ("zip", "tar.gz"):
            raise UsageError("unknown archive format %r, use zip or tar.gz" % fmt)
        ext = "zip" if fmt == "zip" else "tar.gz"
        dest = opts.get("output") or os.path.join(directory, "%s-%s.%s" % (name, tag.lstrip("v"), ext))
        if dest == "-":
            tmp = tempfile.mkdtemp(prefix="gh-forge-archive.")
            try:
                f = os.path.join(tmp, "a")
                _download(["https://codeload.github.com/%s/%s/%s/refs/tags/%s" % (owner, name, "zip" if fmt == "zip" else "tar.gz", tag)], f)
                with open(f, "rb") as fh:
                    shutil.copyfileobj(fh, sys.stdout.buffer)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            return 0
        if os.path.exists(dest):
            if opts.get("skip-existing"):
                return 0
            if not opts.get("clobber"):
                raise GhError("%s already exists (use `--clobber` to overwrite file or "
                              "`--skip-existing` to skip file)" % dest)
        _download(["https://codeload.github.com/%s/%s/%s/refs/tags/%s" %
                   (owner, name, "zip" if fmt == "zip" else "tar.gz", tag)], dest)
        return 0

    patterns = opts.get("pattern") or []
    assets = [a for a in doc.get("assets", [])
              if not patterns or any(fnmatch.fnmatch(a["name"], p) for p in patterns)]
    if not assets:
        raise GhError("no assets match the file pattern")
    if opts.get("output") and len(assets) != 1:
        raise GhError("--output requires exactly one matching asset")
    for asset in assets:
        dest = opts.get("output") or os.path.join(directory, _safe_name(asset["name"]))
        if dest != "-" and os.path.exists(dest):
            if opts.get("skip-existing"):
                continue
            if not opts.get("clobber"):
                raise GhError("%s already exists (use `--clobber` to overwrite file or "
                              "`--skip-existing` to skip file)" % dest)
        if dest == "-":
            tdir = tempfile.mkdtemp(prefix="gh-forge-asset.")
            try:
                tmp = os.path.join(tdir, "a")
                _download([asset["browser_download_url"]], tmp)
                with open(tmp, "rb") as fh:
                    shutil.copyfileobj(fh, sys.stdout.buffer)
            finally:
                shutil.rmtree(tdir, ignore_errors=True)
        else:
            _download([asset["browser_download_url"]], dest)
    return 0


def _gist_spec():
    s = Spec()
    s.value("filename", "f")
    s.flag("raw")
    s.flag("web", "w")
    s.flag("files")
    return s


def gist_view(argv, ctx):
    if argv and argv[0] in ("-h", "--help"):
        output.write(GIST_USAGE)
        return 0
    opts, args = split(argv, _gist_spec())
    if opts.get("web"):
        raise UsageError("--web needs a browser; the read-only forge cannot open one")
    if not args:
        raise UsageError("gist id or URL required")
    gist_id = args[0].rstrip("/").split("/")[-1]
    doc = ctx.backend.data("gists/%s" % gist_id)
    files = doc.get("files") or {}
    if opts.get("files"):
        for fname in files:
            output.write(fname)
        return 0
    wanted = opts.get("filename")
    if wanted and wanted not in files:
        raise GhError("gist has no file %r" % wanted)
    chosen = [wanted] if wanted else list(files)
    for fname in chosen:
        if len(chosen) > 1 and not opts.get("raw"):
            output.write("%s\n" % fname)
        text = files[fname].get("content")
        if text is None:
            raw_url = files[fname].get("raw_url")
            tdir = tempfile.mkdtemp(prefix="gh-forge-gist.")
            try:
                tmp = os.path.join(tdir, "a")
                _download([raw_url], tmp)
                with open(tmp, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            finally:
                shutil.rmtree(tdir, ignore_errors=True)
        output.write_raw(text if text.endswith("\n") else text + "\n")
    return 0
