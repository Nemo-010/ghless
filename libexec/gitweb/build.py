"""Build the static site for one repository."""
import html
import json
import os
import posixpath
import re
import shutil
import urllib.parse

from . import copies, gitdata, md
from .theme import crumbs, esc, page

README_NAMES = ("README.md", "README", "README.txt", "readme.md", "Readme.md")
COMMIT_PAGES = 50
COMMIT_LIST = 200
RECENT = 8


def q(path):
    return urllib.parse.quote(path)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


class Site:
    """Paths and URLs of one repository's pages. `depth` counts directories below the repo site root."""

    def __init__(self, out, name, base_url):
        self.out, self.name, self.base_url = out, name, base_url.rstrip("/")

    def up(self, depth):
        return "../" * depth

    def abs(self, rel):
        if not self.base_url:
            return f"<this-site>/{rel}"
        return f"{self.base_url}/{rel}"


def ensure_bare(out, name, src):
    bare = os.path.join(out, f"{name}.git")
    if os.path.isdir(bare):
        gitdata.git("fetch", "-q", "--force", "--tags", src, "+refs/heads/*:refs/heads/*", cwd=bare)
    else:
        gitdata.git("clone", "-q", "--bare", src, bare)
    gitdata.git("remote", "remove", "origin", cwd=bare, check=False)
    try:
        os.remove(os.path.join(bare, "FETCH_HEAD"))
    except FileNotFoundError:
        pass
    # HEAD is a raw sha: dumb-HTTP clones resolve a symref HEAD only when the
    # loose ref file exists, and slashed branch names confuse some clients.
    src_head = gitdata.git("symbolic-ref", "-q", "HEAD", cwd=src, check=False).decode().strip()
    head_sha = ""
    branch = ""
    if src_head.startswith("refs/heads/"):
        head_sha = gitdata.git("rev-parse", "-q", "--verify", src_head, cwd=bare, check=False).decode().strip()
        branch = src_head[len("refs/heads/"):]
    if not head_sha:
        branches = gitdata.git("for-each-ref", "--format=%(refname)", "refs/heads", cwd=bare).decode().split()
        if not branches:
            raise SystemExit(f"{name}: no branches")
        gitdata.git("symbolic-ref", "HEAD", branches[0], cwd=bare)
        head_sha = gitdata.git("rev-parse", "-q", "--verify", "HEAD", cwd=bare).decode().strip()
        branch = branches[0][len("refs/heads/"):]
    with open(os.path.join(bare, "HEAD"), "w", encoding="ascii") as fh:
        fh.write(head_sha + "\n")
    gitdata.git("update-server-info", cwd=bare)
    return bare, branch


def build_tree(files):
    """{dir: ([subdirs], [(file, size)])} for every directory including the root ''."""
    tree = {"": ([], [])}
    for path, size in files:
        parent = posixpath.dirname(path)
        parts = parent.split("/") if parent else []
        for k in range(len(parts)):
            d, sub = "/".join(parts[:k]), "/".join(parts[:k + 1])
            tree.setdefault(d, ([], []))
            if sub not in tree[d][0]:
                tree[d][0].append(sub)
            tree.setdefault(sub, ([], []))
        tree[parent][1].append((path, size))
    return tree


def src_page(site, path, data, is_md):
    depth = path.count("/") + 1
    up = site.up(depth)
    lines = data.decode("utf-8", "replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    code = "".join(f'<span class="l" id="L{n}">{html.escape(t) or " "}</span>' for n, t in enumerate(lines, 1))
    actions = [f'<a href="{up}raw/{q(path)}">Raw</a>']
    if is_md:
        actions.insert(0, f'<a href="{up}doc/{q(path)}.html">Rendered</a>')
    bar = (f'<div class="bar"><span class="mono">{len(lines):,} lines · {human(len(data))}</span>'
           f'<span>{" · ".join(actions)}</span></div>')
    nav = crumbs(path_crumbs(site, path, up, "view"))
    body = f'<h1 class="mono" style="font-size:1.2rem">{esc(path)}</h1>{bar}<pre class="src">{code}</pre>'
    return page(f"{site.name}: {path}", body, nav, f"{path} in {site.name}")


def path_crumbs(site, path, up, kind):
    parts = [("All repositories", f"{up}../index.html"), (site.name, f"{up}index.html")]
    pieces = path.split("/")
    for k, piece in enumerate(pieces[:-1]):
        parts.append((piece, f"{up}tree/{q('/'.join(pieces[:k + 1]))}/index.html"))
    parts.append((pieces[-1], None))
    return parts


def doc_page(site, path, data, resolver):
    depth = path.count("/") + 1
    up = site.up(depth)
    rendered, heads = md.render(data.decode("utf-8", "replace"), resolver(posixpath.dirname(path), depth),
                                resolver(posixpath.dirname(path), depth, raw=True))
    bar = (f'<div class="bar"><span class="mono">{esc(path)}</span>'
           f'<span><a href="{up}view/{q(path)}.html">Source</a> · <a href="{up}raw/{q(path)}">Raw</a></span></div>')
    nav = crumbs(path_crumbs(site, path, up, "doc"))
    return page(f"{site.name}: {path}", f'{bar}<div class="readme">{rendered}</div>', nav, f"{path} in {site.name}")


def listing(site, d, tree, viewable, depth):
    """Rows for directory `d`: folders first, then files."""
    up = site.up(depth)
    subdirs, entries = tree[d]
    rows = []
    for sub in sorted(subdirs):
        rows.append(f'<tr><td class="kind" aria-hidden="true">▸</td><td><a href="{up}tree/{q(sub)}/index.html">{esc(posixpath.basename(sub))}/</a></td><td class="num"></td></tr>')
    for path, size in sorted(entries):
        label = esc(posixpath.basename(path))
        if path in viewable:
            cell = f'<a href="{up}view/{q(path)}.html">{label}</a>'
        else:
            cell = f'{label} <span class="mono">(not shown)</span>'
        rows.append(f'<tr><td class="kind" aria-hidden="true"> </td><td>{cell}</td><td class="num">{human(size)}</td></tr>')
    if not rows:
        return '<p class="empty">Empty.</p>'
    return f'<div class="tablewrap"><table class="files"><thead><tr><th></th><th>name</th><th class="num">size</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def diff_html(text):
    out = []
    for line in text.split("\n"):
        e = html.escape(line)
        if line.startswith("diff --git"):
            out.append(f'<span class="f">{e}</span>')
        elif line.startswith("@@"):
            out.append(f'<span class="h">{e}</span>')
        elif line.startswith("+") and not line.startswith("+++"):
            out.append(f'<span class="a">{e}</span>')
        elif line.startswith("-") and not line.startswith("---"):
            out.append(f'<span class="d">{e}</span>')
        else:
            out.append(f"<span>{e}</span>")
    return '<pre class="diff">' + "".join(out) + "</pre>"


def commit_table(rows, up, linkable):
    body = ""
    for c in rows:
        sha = html.escape(c["short"])
        cell = f'<a href="{up}commit/{c["short"]}.html">{sha}</a>' if c["short"] in linkable else sha
        body += (f'<tr><td class="hash mono">{cell}</td><td class="date">{html.escape(c["date"])}</td>'
                 f'<td>{html.escape(c["subject"])}<br><span class="mono" style="color:var(--muted)">{html.escape(c["author"])}</span></td></tr>')
    return f'<div class="tablewrap"><table><thead><tr><th>commit</th><th class="date">date</th><th>message</th></tr></thead><tbody>{body}</tbody></table></div>'


def agent_block(site, readme_name, depth=0):
    n = site.name
    lines = [f"git clone {site.abs(q(n) + '.git')}",
             f"curl -O {site.abs(q(n) + '/' + q(n) + '.zip')}",
             f"curl {site.abs(q(n) + '/index.json')}      # machine-readable summary",
             f"curl {site.abs(q(n) + '/files.txt')}       # every path at HEAD"]
    if readme_name:
        lines.append(f"curl {site.abs(q(n) + '/raw/' + q(readme_name))}")
    lines.append(f"curl {site.abs(q(n) + '/raw/PATH')}   # any text file, no HTML")
    note = "" if site.base_url else '<p class="lead">Replace <code>&lt;this-site&gt;</code> with the address in your browser bar, up to and including the host.</p>'
    return ('<details class="agents" open><summary>For scripts and agents</summary>'
            f'<pre class="cmd">{html.escape(chr(10).join(lines))}</pre>{note}'
            f'<p class="lead">Also see <a href="llms.txt">llms.txt</a>. Line anchors work: <code>view/PATH.html#L10</code>.</p></details>')


def build_repo(out, name, src, ledger_rows=(), downloads=(), base_url=""):
    bare, branch = ensure_bare(out, name, src)
    site = Site(out, name, base_url)
    root = os.path.join(out, name)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)

    with open(os.path.join(root, f"{name}.zip"), "wb") as fh:
        fh.write(gitdata.git("archive", "--format=zip", f"--prefix={name}/", "HEAD", cwd=bare))

    files = gitdata.tree_entries(bare)
    tree = build_tree(files)
    viewable, markdown, blobs = set(), set(), set()
    with gitdata.BlobReader(bare) as reader:
        for path, size in files:
            if size > gitdata.MAX_RAW:
                continue
            data = reader.read(f"HEAD:{path}")
            if data is None:
                continue
            blobs.add(path)
            dest = os.path.join(root, "raw", path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data)
            if gitdata.is_text(data) and len(data) <= gitdata.MAX_VIEW:
                viewable.add(path)
                if path.lower().endswith((".md", ".markdown")):
                    markdown.add(path)

    def resolver(base_dir, depth, raw=False):
        up = site.up(depth)

        def resolve(target):
            frag = ""
            if "#" in target:
                target, frag = target.split("#", 1)
                frag = "#" + frag
            target = target.split("?", 1)[0]
            if not target:
                return frag
            target = urllib.parse.unquote(target)
            full = posixpath.normpath(posixpath.join(base_dir, target)) if not target.startswith("/") else posixpath.normpath(target.lstrip("/"))
            if full.startswith(".."):
                return None
            full = "" if full == "." else full
            if raw:
                return f"{up}raw/{q(full)}" if full in blobs else None
            if full in markdown:
                return f"{up}doc/{q(full)}.html{frag}"
            if full in viewable:
                return f"{up}view/{q(full)}.html{frag}"
            if full in tree:
                return f"{up}tree/{q(full)}/index.html" if full else f"{up}index.html"
            if full in blobs:
                return f"{up}raw/{q(full)}"
            return None
        return resolve

    with gitdata.BlobReader(bare) as reader:
        for path in sorted(viewable):
            data = reader.read(f"HEAD:{path}")
            write(os.path.join(root, "view", path + ".html"), src_page(site, path, data, path in markdown))
            if path in markdown:
                write(os.path.join(root, "doc", path + ".html"), doc_page(site, path, data, resolver))

    def readme_text(full):
        with open(os.path.join(root, "raw", full), "rb") as fh:
            return fh.read().decode("utf-8", "replace")

    def readme_for(d, depth):
        for cand in README_NAMES:
            full = posixpath.join(d, cand) if d else cand
            if full in viewable:
                text = readme_text(full)
                if full in markdown or cand.lower() == "readme.md":
                    rendered, heads = md.render(text, resolver(d, depth), resolver(d, depth, raw=True))
                    toc = ""
                    items = [h for h in heads if h[0] in (2, 3)]
                    if len([h for h in heads if h[0] == 2]) >= 3:
                        toc = ('<details class="toc"><summary>Contents</summary><ul>' +
                               "".join(f'<li><a href="#{i}">{html.escape(t)}</a></li>' for _, i, t in items) +
                               "</ul></details>")
                    inner = toc + rendered
                else:
                    inner = f"<pre>{html.escape(text)}</pre>"
                return cand, f'<h2 id="readme">{html.escape(cand)}</h2><div class="readme">{inner}</div>'
        return "", ""

    for d in tree:
        if d == "":
            continue
        depth = d.count("/") + 2
        up = site.up(depth)
        parts = [("All repositories", f"{up}../index.html"), (name, f"{up}index.html")]
        pieces = d.split("/")
        for k, piece in enumerate(pieces[:-1]):
            parts.append((piece, f"{up}tree/{q('/'.join(pieces[:k + 1]))}/index.html"))
        parts.append((pieces[-1], None))
        _, rd = readme_for(d, depth)
        body = f'<h1 class="mono" style="font-size:1.3rem">{esc(d)}/</h1>{listing(site, d, tree, viewable, depth)}{rd}'
        write(os.path.join(root, "tree", d, "index.html"), page(f"{name}/{d}", body, crumbs(parts), f"Directory {d} in {name}"))

    dl_dir = os.path.join(root, "downloads")
    for dl in downloads:
        os.makedirs(dl_dir, exist_ok=True)
        shutil.copy(dl, os.path.join(dl_dir, os.path.basename(dl)))
    links = copies.copy_links(name, ledger_rows, downloads)

    all_commits = gitdata.commits(bare, COMMIT_LIST)
    total = gitdata.commit_count(bare)
    head = all_commits[0] if all_commits else {"sha": "", "short": "", "date": "", "subject": "", "author": ""}
    linkable = {c["short"] for c in all_commits[:COMMIT_PAGES]}
    for c in all_commits[:COMMIT_PAGES]:
        msg, diff, cut = gitdata.show(bare, c["sha"])
        note = '<p class="empty">Diff cut at 256 KiB. Clone the repository for the rest.</p>' if cut else ""
        nav = crumbs([("All repositories", "../../index.html"), (name, "../index.html"), ("Commits", "../commits/index.html"), (c["short"], None)])
        body = (f'<h1>{html.escape(c["subject"])}</h1><div class="facts"><span><b class="mono">{html.escape(c["short"])}</b></span>'
                f'<span>{html.escape(c["author"])}</span><span>{html.escape(c["date"])}</span></div>'
                f'<pre class="wrap">{html.escape(msg)}</pre>{diff_html(diff)}{note}')
        write(os.path.join(root, "commit", c["short"] + ".html"), page(f'{name}: {c["short"]}', body, nav, c["subject"]))
    nav = crumbs([("All repositories", "../../index.html"), (name, "../index.html"), ("Commits", None)])
    cbody = (f'<h1>Commits</h1><p class="lead">Showing {len(all_commits)} of {total}. '
             f'The first {min(COMMIT_PAGES, len(all_commits))} link to their diff; clone for the rest.</p>'
             f'{commit_table(all_commits, "../", linkable)}')
    write(os.path.join(root, "commits", "index.html"), page(f"{name}: commits", cbody, nav))

    branches, tags = gitdata.refs(bare, "heads"), gitdata.refs(bare, "tags")

    def ref_list(items):
        if not items:
            return '<p class="empty">None.</p>'
        rows = "".join(f'<tr><td class="mono">{html.escape(n)}</td><td class="hash mono">{html.escape(s)}</td><td class="date">{html.escape(d)}</td></tr>' for n, s, d in items[:20])
        more = f'<p class="empty">and {len(items) - 20} more</p>' if len(items) > 20 else ""
        return f'<div class="tablewrap"><table><tbody>{rows}</tbody></table></div>{more}'

    readme_name, readme_html = readme_for("", 0)
    n = esc(name)
    facts = (f'<div class="facts"><span>branch <b class="mono">{esc(branch)}</b></span>'
             f'<span>HEAD <b class="mono">{esc(head["short"])}</b></span>'
             f'<span><a href="commits/index.html"><b>{total}</b> commits</a></span>'
             f'<span><b>{len(files)}</b> files</span><span>updated <b>{esc(head["date"])}</b></span></div>')
    clone = f"git clone {site.abs(q(name) + '.git')}"
    get = (f'<h2 id="get">Get the code</h2><pre class="cmd">{html.escape(clone)}</pre>'
           f'<p><a href="{q(name)}.zip">Download {n}.zip</a> (HEAD, {human(os.path.getsize(os.path.join(root, name + ".zip")))}) · '
           f'<a href="files.txt">file list</a> · <a href="index.json">index.json</a></p>')
    files_h = f'<h2 id="files">Files</h2>{listing(site, "", tree, viewable, 0)}'
    refs_h = (f'<h2 id="refs">Branches and tags</h2><div class="grid"><div><h3>Branches</h3>{ref_list(branches)}</div>'
              f'<div><h3>Tags</h3>{ref_list(tags)}</div></div>')
    recent = (f'<h2 id="commits">Recent commits</h2>{commit_table(all_commits[:RECENT], "", linkable)}'
              f'<p><a href="commits/index.html">All commits</a></p>')
    body = (f"<h1>{n}</h1><p class=\"lead\">{esc(head['subject'])}</p>{facts}{get}{agent_block(site, readme_name)}"
            f"{files_h}{readme_html}{copies.links_html(links)}{recent}{refs_h}")
    write(os.path.join(root, "index.html"), page(name, body, crumbs([("All repositories", "../index.html"), (name, None)]),
                                                 f"{name}: {head['subject']}"))

    write(os.path.join(root, "files.txt"), "".join(p + "\n" for p, _ in files))
    summary = {
        "name": name, "branch": branch, "head": head["sha"], "head_short": head["short"],
        "head_subject": head["subject"], "head_date": head["date"], "commits": total, "files": len(files),
        "clone": site.abs(f"{name}.git") if site.base_url else f"{name}.git",
        "zip": f"{name}/{name}.zip", "raw": f"{name}/raw/PATH", "file_list": f"{name}/files.txt",
        "readme": f"{name}/raw/{readme_name}" if readme_name else None,
        "other_copies": [{"label": l, "url": u, "command": t} for l, u, t in links],
        "files_detail": [{"path": p, "bytes": s, "raw": p in blobs, "page": p in viewable} for p, s in files],
    }
    write(os.path.join(root, "index.json"), json.dumps(summary, indent=2) + "\n")
    write(os.path.join(root, "llms.txt"), repo_llms(site, summary, links))
    return name, head["short"], head["subject"], links, summary


def repo_llms(site, s, links):
    n = s["name"]
    base = site.base_url

    def u(path):
        return f"{base}/{path}"

    lines = [f"# {n}", "", f"> {s['head_subject']} (branch {s['branch']}, HEAD {s['head_short']}, {s['commits']} commits, {s['files']} files)", ""]
    if not base:
        lines += ["Addresses start with /: prefix them with the scheme and host this file was fetched from.", ""]
    lines += ["## Fetch", f"- Clone: git clone {u(q(n) + '.git')}",
              f"- Zip of HEAD: {u(q(n) + '/' + q(n) + '.zip')}",
              f"- Summary as JSON: {u(q(n) + '/index.json')}",
              f"- Every path at HEAD: {u(q(n) + '/files.txt')}",
              f"- Any text file as plain text: {u(q(n) + '/raw/PATH')}", ""]
    if links:
        lines.append("## Other copies")
        for label, url, text in links:
            lines.append(f"- {label}: {url or text.replace(chr(10), ' ; ')}")
        lines.append("")
    return "\n".join(lines)
