"""Page shell and stylesheet shared by every generated page. No scripts."""
import html

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
           "%3Crect width='32' height='32' rx='7' fill='%230b5fb3'/%3E%3Cpath d='M9 22V10h5a4 4 0 010 8h-2' "
           "fill='none' stroke='white' stroke-width='3' stroke-linecap='round'/%3E%3C/svg%3E")

CSS = """
:root{color-scheme:light dark;--bg:#fbfbfa;--fg:#1d2327;--muted:#5d6870;--line:#dde1e4;--card:#fff;--code:#f1f3f4;--link:#0b5fb3;--accent:#0b5fb3;--add:#e6f6ea;--del:#fdecec;--hunk:#e8f0fb;--ok:#1a7f37;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#14171a;--fg:#e3e7ea;--muted:#9aa5ad;--line:#2b3238;--card:#1b1f23;--code:#222830;--link:#7db7ff;--accent:#7db7ff;--add:#12261a;--del:#2c1517;--hunk:#17233a;--ok:#56d364;--warn:#d29922}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-padding-top:1rem}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main,header .in,footer{max-width:64rem;margin:0 auto;padding:0 1rem}
header{border-bottom:1px solid var(--line);background:var(--card)}
header .in{padding-top:.7rem;padding-bottom:.7rem}
nav.crumbs{font-size:.92rem;color:var(--muted);overflow-wrap:anywhere}
nav.crumbs a+a:before,nav.crumbs a+span:before,nav.crumbs span+span:before{content:"/";margin:0 .4rem;color:var(--line)}
nav.crumbs span[aria-current]{color:var(--fg);font-weight:600}
.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:.4rem .8rem;border:2px solid var(--accent);z-index:9}
a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
h1{font-size:1.8rem;line-height:1.25;margin:1.4rem 0 .3rem;overflow-wrap:anywhere}
h2{font-size:1.15rem;margin:2.2rem 0 .7rem;padding-bottom:.3rem;border-bottom:1px solid var(--line)}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em}
code{background:var(--code);padding:.08rem .3rem;border-radius:4px;overflow-wrap:anywhere}
pre{background:var(--code);padding:.9rem 1rem;border-radius:8px;overflow-x:auto;max-width:100%;margin:.6rem 0;line-height:1.5}
pre code{background:none;padding:0;overflow-wrap:normal}
pre.cmd{user-select:all;-webkit-user-select:all;cursor:text;border:1px solid var(--line)}
p.lead{color:var(--muted);margin:.2rem 0 1rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.2rem 1rem;margin:.6rem 0}
.facts{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;color:var(--muted);font-size:.9rem;margin:.3rem 0 1rem}
.facts b{color:var(--fg);font-weight:600}
.pill{display:inline-block;font-size:.75rem;padding:.05rem .5rem;border:1px solid var(--line);border-radius:99px;color:var(--muted);margin-right:.3rem;vertical-align:middle}
.pill.ok{color:var(--ok);border-color:var(--ok)}.pill.warn{color:var(--warn);border-color:var(--warn)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(18rem,1fr));gap:1rem}
.grid>div>h3{margin:.8rem 0 .2rem;font-size:.95rem}
details.agents{border:1px dashed var(--line);border-radius:10px;padding:.5rem 1rem;margin:1rem 0;background:var(--card)}
details.agents summary{cursor:pointer;font-weight:600}
dl.copies{margin:0}
dl.copies div{padding:.7rem 0;border-bottom:1px solid var(--line)}
dl.copies div:last-child{border-bottom:0}
dl.copies dt{font-weight:600}
dl.copies dd{margin:.15rem 0 0;overflow-wrap:anywhere}
dl.copies dd.url{font-size:.85rem}
table{width:100%;border-collapse:collapse}
th{white-space:nowrap;font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);text-align:left}
th,td{padding:.4rem .6rem .4rem 0;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:break-word}
td code{overflow-wrap:break-word}
td.num,th.num{text-align:right;white-space:nowrap;color:var(--muted)}
td.hash,td.date{white-space:nowrap;color:var(--muted)}
td.kind{width:1.6rem;color:var(--muted)}
.tablewrap{overflow-x:auto}
table.files tr:hover td{background:var(--code)}
.readme{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.2rem 1.4rem 1rem;overflow-wrap:anywhere}
.readme h1,.readme h2{margin-top:1.6rem}
.readme img{max-width:100%;height:auto}
.readme blockquote{margin:.8rem 0;padding:.1rem 1rem;border-left:4px solid var(--line);color:var(--muted)}
.readme table{margin:.8rem 0}.readme th{text-transform:none;font-size:.9rem;letter-spacing:0;color:var(--fg)}
.anchor{margin-left:.4rem;opacity:0;font-weight:400}h1:hover .anchor,h2:hover .anchor,h3:hover .anchor,.anchor:focus{opacity:.6}
.toc{font-size:.92rem;margin:.8rem 0}.toc summary{cursor:pointer;color:var(--muted)}.toc ul{margin:.3rem 0;padding-left:1.2rem}
.src{counter-reset:ln;padding:.6rem 0}
.src span.l{display:block;padding:0 1rem 0 0;min-height:1.5em}
.src span.l:before{counter-increment:ln;content:counter(ln);display:inline-block;width:3.6rem;padding-right:.9rem;text-align:right;color:var(--muted);user-select:none;-webkit-user-select:none}
.src span.l:target{background:var(--hunk)}
.src span.l:before{text-decoration:none}
.bar{display:flex;flex-wrap:wrap;gap:.4rem 1rem;align-items:center;justify-content:space-between;margin:.6rem 0}
.diff span{display:block;white-space:pre}
.diff .a{background:var(--add)}.diff .d{background:var(--del)}.diff .h{background:var(--hunk);color:var(--muted)}.diff .f{font-weight:700;margin-top:.8rem}
.empty{color:var(--muted);font-style:italic}
footer{color:var(--muted);font-size:.85rem;padding-top:1.5rem;padding-bottom:2.5rem}
@media (max-width:40rem){body{font-size:15px}h1{font-size:1.45rem}td.date,th.date{display:none}.readme{padding:.1rem .8rem .8rem}}
@media print{header,footer,details.agents,.skip{display:none}body{background:#fff;color:#000}}
"""


def esc(text):
    return html.escape(text, quote=True)


def crumbs(parts):
    """parts: [(label, href or None)]; the last one is the current page."""
    out = []
    for k, (label, href) in enumerate(parts):
        last = k == len(parts) - 1
        if last or not href:
            out.append(f'<span aria-current="page">{esc(label)}</span>' if last else f"<span>{esc(label)}</span>")
        else:
            out.append(f'<a href="{esc(href)}">{esc(label)}</a>')
    return '<nav class="crumbs" aria-label="Breadcrumb">' + "".join(out) + "</nav>"


def page(title, body, nav_html, description="", head_extra=""):
    desc = f'<meta name="description" content="{esc(description)}">' if description else ""
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{esc(title)}</title>{desc}"
            f"<meta property=\"og:title\" content=\"{esc(title)}\">"
            f"<link rel=\"icon\" href=\"{FAVICON}\">{head_extra}<style>{CSS}</style></head><body>"
            f"<a class=\"skip\" href=\"#main\">Skip to content</a>"
            f"<header><div class=\"in\">{nav_html}</div></header><main id=\"main\">{body}</main>"
            "<footer>Static pages from ghless. No scripts. Everything here is also plain files: "
            "<code>index.json</code> and <code>llms.txt</code> describe the site for programs.</footer>"
            "</body></html>\n")
