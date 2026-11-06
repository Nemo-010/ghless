"""Small, safe Markdown to HTML converter for README pages.

All text is HTML-escaped before any markup is added. Links and images are
emitted only for http, https, mailto and relative targets. Raw HTML in the
source is shown as text, never passed through.
"""
import html
import re
import urllib.parse

SAFE_SCHEMES = ("http", "https", "mailto")
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+#.-]*)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HRULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def slug(text):
    s = re.sub(r"<[^>]+>", "", text).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", "-", s)


def safe_target(target, resolve):
    target = target.strip()
    if target.startswith("#"):
        return target
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme:
        return target if parsed.scheme.lower() in SAFE_SCHEMES else None
    if target.startswith("//"):
        return None
    return resolve(target) if resolve else target


def inline(text, resolve=None, image_resolve=None):
    stash = []

    def keep(fragment):
        stash.append(fragment)
        return f"\0{len(stash) - 1}\0"

    text = re.sub(r"(`+)(.+?)\1", lambda m: keep(f"<code>{html.escape(m.group(2).strip())}</code>"), text)

    def image(m):
        src = safe_target(html.unescape(m.group(2)), image_resolve or resolve)
        alt = m.group(1).replace('"', "&quot;")
        if src is None:
            return alt
        return keep(f'<img src="{html.escape(src, quote=True)}" alt="{alt}" loading="lazy">')

    def link(m):
        href = safe_target(html.unescape(m.group(2)), resolve)
        label = m.group(1)
        if href is None:
            return label
        return keep(f'<a href="{html.escape(href, quote=True)}">') + label + keep("</a>")

    text = html.escape(text, quote=False)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", image, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", link, text)
    text = re.sub(r"&lt;(https?://[^\s<>]+?)&gt;", lambda m: keep(
        f'<a href="{html.escape(html.unescape(m.group(1)), quote=True)}">{m.group(1)}</a>'), text)
    text = re.sub(r"(?<![\"'>=\w])(https?://[^\s<)\]]+[^\s<)\].,;:!?])",
                  lambda m: keep(f'<a href="{html.escape(html.unescape(m.group(1)), quote=True)}">{m.group(1)}</a>'), text)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])",
                  lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
    return re.sub(r"\0(\d+)\0", lambda m: stash[int(m.group(1))], text)


def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render(source, resolve=None, image_resolve=None):
    """Return (html, headings) for Markdown `source`. headings is [(level, id, text)]."""
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, headings, para = [], [], []
    seen_ids = {}

    def inl(t):
        return inline(t, resolve, image_resolve)

    def flush():
        if para:
            out.append("<p>" + inl(" ".join(p.strip() for p in para)) + "</p>")
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = FENCE.match(line)
        if fence:
            flush()
            marker, lang = fence.group(2), fence.group(3)
            body = []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang, quote=True)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body))}</code></pre>")
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        h = HEADING.match(line)
        if h:
            flush()
            level, text = len(h.group(1)), h.group(2)
            base = slug(text) or "section"
            n = seen_ids.get(base, 0)
            seen_ids[base] = n + 1
            hid = base if n == 0 else f"{base}-{n}"
            headings.append((level, hid, re.sub(r"[`*_]", "", text)))
            out.append(f'<h{level} id="{hid}">{inl(text)}<a class="anchor" href="#{hid}" aria-label="link to this section">#</a></h{level}>')
            i += 1
            continue
        if HRULE.match(line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner, _ = render("\n".join(quote), resolve, image_resolve)
            out.append(f"<blockquote>{inner}</blockquote>")
            continue
        if "|" in line and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]) and "|" in lines[i + 1]:
            flush()
            head = split_row(line)
            aligns = []
            for cell in split_row(lines[i + 1]):
                aligns.append("right" if cell.endswith(":") and not cell.startswith(":") else "center" if cell.startswith(":") and cell.endswith(":") else "")
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(split_row(lines[i]))
                i += 1

            def cell(tag, text, k):
                a = aligns[k] if k < len(aligns) and aligns[k] else ""
                style = f' style="text-align:{a}"' if a else ""
                return f"<{tag}{style}>{inl(text)}</{tag}>"

            thead = "".join(cell("th", c, k) for k, c in enumerate(head))
            tbody = "".join("<tr>" + "".join(cell("td", c, k) for k, c in enumerate(r)) + "</tr>" for r in rows)
            out.append(f'<div class="tablewrap"><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>')
            continue
        item = ITEM.match(line)
        interrupts = item and (not para or item.group(2) in "-*+" or item.group(2)[:-1] == "1")
        if item and interrupts:
            flush()
            i, block = list_block(lines, i, resolve, image_resolve)
            out.append(block)
            continue
        para.append(line)
        i += 1
    flush()
    return "\n".join(out), headings


def list_block(lines, i, resolve, image_resolve):
    """Render a (possibly nested) list starting at lines[i]; return (next index, html)."""
    first = ITEM.match(lines[i])
    base_indent = len(first.group(1).expandtabs(4))
    ordered = first.group(2)[0].isdigit()
    tag = "ol" if ordered else "ul"
    items = []
    while i < len(lines):
        m = ITEM.match(lines[i])
        if not m or len(m.group(1).expandtabs(4)) != base_indent or m.group(2)[0].isdigit() != ordered:
            break
        text = [m.group(3)]
        nested = ""
        i += 1
        while i < len(lines) and lines[i].strip():
            nm = ITEM.match(lines[i])
            if nm and len(nm.group(1).expandtabs(4)) > base_indent:
                i, nested = list_block(lines, i, resolve, image_resolve)
                continue
            if nm:
                break
            if len(lines[i]) - len(lines[i].lstrip()) > base_indent:
                text.append(lines[i].strip())
                i += 1
                continue
            break
        body = inline(" ".join(text), resolve, image_resolve)
        box = re.match(r"^\[([ xX])\]\s+", " ".join(text))
        if box:
            checked = "checked " if box.group(1) != " " else ""
            body = f'<input type="checkbox" disabled {checked}> ' + inline(" ".join(text)[box.end():], resolve, image_resolve)
        items.append(f"<li>{body}{nested}</li>")
        if i < len(lines) and not lines[i].strip():
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            nm = ITEM.match(lines[j]) if j < len(lines) else None
            if nm and len(nm.group(1).expandtabs(4)) == base_indent:
                i = j
    return i, f"<{tag}>{''.join(items)}</{tag}>"
