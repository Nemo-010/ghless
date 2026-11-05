#!/usr/bin/env python3
"""Build a static git web index: browsable pages plus dumb-HTTP clonable repos.

usage: gitweb-static.py OUTDIR NAME=REPO_PATH [NAME=REPO_PATH ...]

For each repo it writes, under OUTDIR:
  NAME.git/            bare repo with update-server-info run (git clone works over plain HTTP)
  NAME/index.html      README, recent commits, clone commands, file list
  NAME/view/PATH.html  every text file under 512 KiB, escaped
  NAME/NAME.zip        git archive of HEAD
and OUTDIR/index.html listing every repo. The output is static files only.
"""
import html
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

MAX_VIEW = 512 * 1024
CSS = ("body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}"
       "pre{background:#f4f4f4;padding:.8rem;overflow:auto}code{background:#f4f4f4;padding:0 .2rem}"
       "table{border-collapse:collapse}td,th{padding:.15rem .8rem .15rem 0;text-align:left}")


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def href(path: str) -> str:
    return html.escape(urllib.parse.quote(path), quote=True)


def git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check,
                          capture_output=True).stdout


def page(title, body, depth=0):
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style><body>{body}")


def is_text(data: bytes) -> bool:
    return b"\0" not in data[:8000]


def build_repo(out, name, src):
    bare = os.path.join(out, f"{name}.git")
    if os.path.isdir(bare):
        git("fetch", "-q", "--force", "--tags", src, "+refs/heads/*:refs/heads/*", cwd=bare)
        git("remote", "remove", "origin", cwd=bare, check=False)
        try:
            os.remove(os.path.join(bare, "FETCH_HEAD"))
        except FileNotFoundError:
            pass
    else:
        git("clone", "-q", "--bare", src, bare)
        git("remote", "remove", "origin", cwd=bare, check=False)
    head = git("symbolic-ref", "-q", "HEAD", cwd=bare, check=False).decode().strip()
    if not head or not git("rev-parse", "-q", "--verify", head, cwd=bare, check=False):
        branches = git("for-each-ref", "--format=%(refname)", "refs/heads", cwd=bare).decode().split()
        if not branches:
            raise SystemExit(f"{name}: no branches")
        git("symbolic-ref", "HEAD", branches[0], cwd=bare)
    git("update-server-info", cwd=bare)

    site = os.path.join(out, name)
    shutil.rmtree(site, ignore_errors=True)
    os.makedirs(site)
    zip_path = os.path.join(site, f"{name}.zip")
    with open(zip_path, "wb") as fh:
        fh.write(git("archive", "--format=zip", f"--prefix={name}/", "HEAD", cwd=bare))

    entries = git("ls-tree", "-r", "-z", "HEAD", cwd=bare).decode().split("\0")
    files = [e.split("\t", 1)[1] for e in entries if e and e.split(" ", 1)[0] != "160000"]
    rows = []
    for f in files:
        if f.startswith("/") or ".." in f.split("/"):
            continue
        data = git("cat-file", "blob", f"HEAD:{f}", cwd=bare)
        if is_text(data) and len(data) <= MAX_VIEW:
            dest = os.path.join(site, "view", f + ".html")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(page(f, f"<p><a href='{'../' * (f.count('/') + 1)}index.html'>{html.escape(name)}</a> / "
                                 f"<b>{html.escape(f)}</b></p><pre>{html.escape(data.decode('utf-8', 'replace'))}</pre>"))
            rows.append(f"<tr><td><a href='view/{href(f)}.html'>{html.escape(f)}</a></td><td>{len(data)}</td></tr>")
        else:
            rows.append(f"<tr><td>{html.escape(f)} (not shown)</td><td>{len(data)}</td></tr>")

    readme = ""
    for cand in ("README.md", "README", "README.txt", "readme.md"):
        r = git("cat-file", "blob", f"HEAD:{cand}", cwd=bare, check=False)
        if r:
            readme = f"<h2>{cand}</h2><pre>{html.escape(r.decode('utf-8', 'replace'))}</pre>"
            break
    log = git("log", "-n", "30", "--format=%h%x09%an%x09%ad%x09%s", "--date=short", cwd=bare).decode()
    log_rows = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in line.split("\t", 3)) + "</tr>"
                       for line in log.splitlines())
    body = (f"<p><a href='../index.html'>all repositories</a></p><h1>{html.escape(name)}</h1>"
            f"<p>Clone: <code>git clone &lt;this-site&gt;/{html.escape(name)}.git</code> &middot; "
            f"<a href='{href(name)}.zip'>zip of HEAD</a></p>"
            f"{readme}<h2>Recent commits</h2><table>{log_rows}</table>"
            f"<h2>Files ({len(files)})</h2><table>{''.join(rows)}</table>")
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(name, body))
    return name, git("rev-parse", "--short", "HEAD", cwd=bare).decode().strip(), \
        git("log", "-1", "--format=%s", cwd=bare).decode().strip()


def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    items, failed = [], []
    for spec in sys.argv[2:]:
        name, _, src = spec.partition("=")
        if not NAME_RE.match(name) or not src:
            print(f"refused spec {spec!r}: name must match {NAME_RE.pattern}", file=sys.stderr)
            failed.append(name)
            continue
        existed = os.path.isdir(os.path.join(out, f"{name}.git"))
        try:
            items.append(build_repo(out, name, src))
        except (subprocess.CalledProcessError, SystemExit) as exc:
            if not existed:
                shutil.rmtree(os.path.join(out, f"{name}.git"), ignore_errors=True)
                shutil.rmtree(os.path.join(out, name), ignore_errors=True)
            detail = exc.stderr.decode(errors="replace").strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            print(f"{name}: not published: {detail}", file=sys.stderr)
            failed.append(name)
    if not items:
        return 1
    rows = "".join(f"<tr><td><a href='{href(n)}/index.html'>{html.escape(n)}</a></td>"
                   f"<td><code>{h}</code></td><td>{html.escape(s)}</td></tr>" for n, h, s in items)
    body = ("<h1>errand git index</h1><p>Every repository here clones over plain HTTP: "
            "<code>git clone &lt;this-site&gt;/NAME.git</code></p>"
            f"<table><tr><th>repo</th><th>HEAD</th><th>last commit</th></tr>{rows}</table>")
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page("errand git index", body))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
