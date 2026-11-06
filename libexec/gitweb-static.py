#!/usr/bin/env python3
"""Build a static git web index: browsable pages plus dumb-HTTP clonable repos.

usage: gitweb-static.py [--ledger FILE] [--download NAME=FILE ...] [--base-url URL] OUTDIR NAME=REPO_PATH [...]

--ledger reads the publish ledger (TSV: time, lane, project, url, status) and adds an
"Other copies" section to each repo page and the index: Radicle, Nostr, Arweave, archives.
--download copies FILE into NAME/downloads/ and links it (archive zip, release zip).
--base-url puts the public address into the clone commands, llms.txt and index.json.

For each repo it writes, under OUTDIR:
  NAME.git/              bare repo with update-server-info run (git clone works over plain HTTP)
  NAME/index.html        facts, clone command, files, rendered README, other copies, commits, branches
  NAME/tree/DIR/         directory listings with their README
  NAME/view/PATH.html    text files under 512 KiB with line numbers and #L anchors
  NAME/doc/PATH.html     Markdown files rendered
  NAME/raw/PATH          the file itself, as text or binary, up to 4 MiB
  NAME/commits/, commit/ history and the diff of the latest 50 commits
  NAME/files.txt, index.json, llms.txt, NAME.zip
and OUTDIR/index.html, index.json and llms.txt listing every repo. The output is static files only.
"""
import html
import json
import os
import shutil
import subprocess
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gitweb import build, copies  # noqa: E402
from gitweb.theme import crumbs, esc, page  # noqa: E402

import re  # noqa: E402

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def href(path: str) -> str:
    return html.escape(urllib.parse.quote(path), quote=True)


def write_index(out, items, base_url):
    rows = ""
    for name, short, subject, links, summary in items:
        rows += (f'<tr><td><a href="{href(name)}/index.html">{html.escape(name)}</a></td>'
                 f'<td class="hash mono">{html.escape(short)}</td><td class="date">{html.escape(summary["head_date"])}</td>'
                 f'<td>{html.escape(subject)}</td></tr>')
    copies_h = "".join(
        f'<h2>{html.escape(n)}: other copies</h2>' + copies.links_html(l, prefix=f"{href(n)}/", heading=False)
        for n, _, _, l, _ in items if l)
    base = base_url.rstrip("/") if base_url else "<this-site>"
    cmds = "\n".join(f"git clone {base}/{n}.git" for n, *_ in items)
    body = ("<h1>Git index</h1><p class=\"lead\">Every repository here clones over plain HTTP and is also "
            "browsable. Pick one, or copy a command.</p>"
            f'<pre class="cmd">{html.escape(cmds)}</pre>'
            + ("" if base_url else '<p class="lead">Replace <code>&lt;this-site&gt;</code> with the address in your browser bar.</p>')
            + f'<div class="tablewrap"><table><thead><tr><th>repository</th><th>HEAD</th><th class="date">date</th><th>last commit</th></tr></thead><tbody>{rows}</tbody></table></div>'
            + copies_h)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page("Git index", body, crumbs([("All repositories", None)]), "Repositories served by ghless",
                      '<link rel="alternate" type="application/json" href="index.json">'))
    index = {"repositories": [{k: v for k, v in s.items() if k != "files_detail"} for *_, s in items]}
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
        fh.write("\n")
    root = base_url.rstrip("/")
    lines = ["# Git index", "", "> Static git hosting. Each repository clones over plain HTTP."]
    if not root:
        lines.append("> Addresses start with /: prefix them with the scheme and host this file was fetched from.")
    lines += ["", "## Repositories"]
    for n, short, subject, _, s in items:
        lines.append(f"- {n}: {subject} (HEAD {short}). Clone: git clone {root}/{n}.git ; details: {root}/{n}/llms.txt")
    with open(os.path.join(out, "llms.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    args = sys.argv[1:]
    ledger, downloads, base_url = None, {}, ""
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--ledger" and args:
            ledger = args.pop(0)
        elif flag == "--base-url" and args:
            base_url = args.pop(0)
        elif flag == "--download" and args:
            n, _, f = args.pop(0).partition("=")
            if os.path.isfile(f):
                downloads.setdefault(n, []).append(f)
        else:
            print(f"unknown option {flag}", file=sys.stderr)
            return 2
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    rows = copies.read_ledger(ledger)
    out = args[0]
    os.makedirs(out, exist_ok=True)
    items, failed = [], []
    for spec in args[1:]:
        name, _, src = spec.partition("=")
        if not NAME_RE.match(name) or not src:
            print(f"refused spec {spec!r}: name must match {NAME_RE.pattern}", file=sys.stderr)
            failed.append(name)
            continue
        existed = os.path.isdir(os.path.join(out, f"{name}.git"))
        try:
            items.append(build.build_repo(out, name, src, rows, downloads.get(name, []), base_url))
        except (subprocess.CalledProcessError, SystemExit) as exc:
            if not existed:
                shutil.rmtree(os.path.join(out, f"{name}.git"), ignore_errors=True)
                shutil.rmtree(os.path.join(out, name), ignore_errors=True)
            detail = exc.stderr.decode(errors="replace").strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            print(f"{name}: not published: {detail}", file=sys.stderr)
            failed.append(name)
    if not items:
        return 1
    write_index(out, items, base_url)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
