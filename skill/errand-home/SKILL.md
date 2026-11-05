---
name: errand-home
description: Read, clone, modify, publish and archive code with no GitHub account. Use when GitHub access is missing, suspended or rate limited, or when work must be published to decentralized hosts and archived.
---

# errand-home

Run `errand doctor` first. If `errand` is not on PATH, run `./install` from the errand-home checkout (once; safe to repeat).

## Do this

| goal | command |
| --- | --- |
| read a file or README | `errand read owner/repo [path] [ref]` |
| read issues, PRs, releases, search | `gh ...` (real gh if its token works, else read-only forge fallback, same output) |
| clone and start work | `errand fetch owner/repo` prints the checkout path. Work on branch `errand/work`; commit there. |
| publish your result | `errand publish <dir> --name <name>` prints one line per lane. Exit 0 means at least one lane worked. |
| public temporary page | `errand serve <name>=<dir>` prints the clone URL (changes on restart) |
| keep a recoverable copy | `errand archive <dir>` (add `--public-url URL` for the published URL) |
| lost account or deleted repo | `ghre ledger ACCOUNT --out work/ACCOUNT`, then `ghre recover` |

## Rules

- Never delete a lane, fallback or script because it looks unneeded. Fix, extend, or report it.
- Writes to GitHub are impossible without a real token. `gh issue create` and similar exit 1 by design.
- Publishing is public and permanent on Radicle seeds, Nostr relays and Arweave. Publish only what may be public.
- Keys live in `$ERRAND_DATA` (default `~/.local/share/errand/data`). Never print or commit them. `errand identity` shows public ids only.
- Do not push to GitHub unless the operator asks. `errand publish` pushes to github only when a real token works.
- A lane result of `failed` is a result, not an error to hide. Report it with its reason.
- Long commands (`archive --deep`, `ghre snapshot --save`) run detached: `setsid -f ... > log 2>&1`.

## Where things are

`README.md` (overview), `docs/LANES.md` (what was observed per lane), `components/*/README.md` (original tools),
`reference/` (survey and field report with raw evidence), `pins/pins.env` (pinned binaries).
