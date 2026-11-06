# The published page

`ghless serve` writes static files into `$GHLESS_DATA/pub` and serves them with `cfrs serve`. The generator is `libexec/gitweb-static.py` (entry) and `libexec/gitweb/` (Markdown renderer, git reader, page builder, stylesheet). It needs python3 and git only. Pages contain no scripts.

## For people

| page | content |
| --- | --- |
| `/index.html` | every repository, one clone command per repository, other copies |
| `/NAME/index.html` | branch, HEAD, commit count, update date, clone command, zip, root file list, rendered README with contents, other copies, recent commits, branches and tags |
| `/NAME/tree/DIR/index.html` | directory listing and the directory's README |
| `/NAME/view/PATH.html` | text file under 512 KiB with line numbers; `#L12` highlights line 12 |
| `/NAME/doc/PATH.html` | a Markdown file rendered; relative links stay inside the site |
| `/NAME/commits/`, `/NAME/commit/SHA.html` | history (200 entries) and the diff of the latest 50 commits |

Dark mode, mobile layout, keyboard skip link and print styles are built in. The clone command is one click to select (`user-select: all`), so no script is needed to copy it.

## For programs and agents

| URL | content |
| --- | --- |
| `/llms.txt`, `/NAME/llms.txt` | plain-text map: clone, zip, JSON, file list, other copies |
| `/index.json`, `/NAME/index.json` | HEAD sha, branch, counts, clone URL, other copies, per-file size and whether a raw copy exists |
| `/NAME/files.txt` | one path per line at HEAD |
| `/NAME/raw/PATH` | the file bytes, up to 4 MiB; no HTML around it |
| `/NAME.git` | `git clone` over plain HTTP |

The tunnel address is only known after `cfrs serve` starts. `ghless serve` rebuilds the pages once the address exists, so the clone commands, `llms.txt` and `index.json` carry it. When the generator runs without `--base-url`, commands show `<this-site>` and `llms.txt` uses paths starting with `/`.

`ghless serve --json` prints `{url, index_status, repositories: [{name, page, clone, summary, llms}]}` on stdout and nothing else.

## Safety

The Markdown renderer escapes all text first. Links and images are emitted only for http, https, mailto and relative targets that resolve to a file or directory in the repository; `javascript:`, `data:` and `//host` targets are dropped. A repository file named `*.html` or `*.svg` is served under `raw/` with the content type the host picks, so serve repositories whose content you trust.

## Limits

- Files over 512 KiB are not shown as pages; over 4 MiB they are not copied to `raw/` (the clone has them).
- No syntax highlighting and no search; both would need scripts or a large renderer.
- Only the root README and directory READMEs are rendered on listing pages.
