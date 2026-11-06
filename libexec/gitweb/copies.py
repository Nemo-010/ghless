"""Links to every other copy of a project, taken from the publish ledger."""
import html
import os
import re
import urllib.parse


def href_name(path):
    return urllib.parse.quote(os.path.basename(path))


def read_ledger(path):
    rows = []
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 5:
                    rows.append(tuple(parts))
    return rows


def copy_links(name, rows, downloads):
    """[(label, href or None, text)] for every other copy of project `name`."""
    links = []
    latest = {}
    for ts, lane, proj, url, status in rows:
        if proj == name and lane != "tunnel":
            latest[lane] = (url, status)
    if "radicle" in latest and latest["radicle"][1].startswith("ok"):
        m = re.search(r"rad:[A-Za-z0-9]+", latest["radicle"][0])
        if m:
            rid = m.group(0)
            links.append(("Radicle (seed API)", f"https://iris.radicle.xyz/api/v1/repos/{rid}", f"rad clone {rid}"))
            links.append(("Radicle (explorer)", f"https://radicle.network/nodes/iris.radicle.xyz/{rid}", ""))
    if "nostr" in latest and latest["nostr"][1].startswith("ok"):
        m = re.match(r"nostr://([^/]+)/([^/]+)/([^/]+)$", latest["nostr"][0])
        if m:
            npub, relay, ident = m.groups()
            links.append(("Nostr (gitworkshop)", f"https://gitworkshop.dev/{npub}/{relay}/{ident}", f"git clone {latest['nostr'][0]}"))
            links.append(("Nostr (GRASP smart-HTTP)", f"https://{relay}/{npub}/{ident}.git/info/refs?service=git-upload-pack", ""))
    if "arweave" in latest and latest["arweave"][1].startswith("ok"):
        found = re.findall(r"arweave://[A-Za-z0-9_-]+/[A-Za-z0-9._-]+", latest["arweave"][0])
        if found:
            label = "Arweave" if len(found) == 1 else f"Arweave ({len(found)} parts, copy every part's top-level entries into one directory)"
            links.append((label, None, "\n".join(f"git clone {u}" for u in found)))
    if "swh" in latest:
        url, status = latest["swh"]
        links.append(("Software Heritage", "https://archive.softwareheritage.org/browse/origin/?origin_url=" + urllib.parse.quote(url, safe=""), "save request: " + status))
    if "wayback" in latest:
        m = re.search(r"https://web\.archive\.org/web/\S+", latest["wayback"][1])
        if m:
            links.append(("Wayback Machine", m.group(0), ""))
    for dl in downloads:
        links.append(("Download", f"downloads/{href_name(dl)}", os.path.basename(dl)))
    return links


def links_html(links, prefix="", heading=True):
    """One <dl> entry per copy: label, full link text (wraps), optional command."""
    if not links:
        return ""
    items = []
    for label, link, text in links:
        parts = [f"<dt>{html.escape(label)}</dt>"]
        if link:
            target = link if link.startswith("http") else prefix + link
            shown = link if link.startswith("http") else text
            parts.append(f"<dd class=\"url\"><a href=\"{html.escape(target, quote=True)}\">{html.escape(shown)}</a></dd>")
            if text and link.startswith("http"):
                parts.append(f"<dd><code>{html.escape(text)}</code></dd>")
        elif text:
            for line in text.split("\n"):
                parts.append(f"<dd><code>{html.escape(line)}</code></dd>")
        items.append("<div>" + "".join(parts) + "</div>")
    head = "<h2 id=\"copies\">Other copies</h2>" if heading else ""
    return head + "<div class=\"card\"><dl class=\"copies\">" + "".join(items) + "</dl></div>"
