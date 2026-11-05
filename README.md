# ghless

One home for an agent that has no GitHub account and still has to read, copy,
change, publish and archive code. It melds five earlier pieces (see
`docs/PROVENANCE.md`) behind one installer and one command, `ghless`.

```sh
./install                                    # once; idempotent; sudo only if system packages are missing
. <prefix>/env.sh                            # the installer prints the exact path; login shells load it from ~/.profile
ghless doctor                                # what works on this host
```

Keep the checkout: the links and the profile block point into it. If you move it, run `./install` again from the new place; stale links are relinked.
Isolated trial (own prefix, nothing shared): `GHLESS_PREFIX=$HOME/ghless-prefix ./install`. The prefix is `$XDG_DATA_HOME/ghless` when that variable points inside `$HOME`, else `~/.local/share/ghless`, so a different `HOME` is always a separate install; the installer prints it.

Fetch the repo from a zip, a decentralized host, or GitHub, run `./install`
once, and the rest needs nothing else.

## What you get

| need | command | how it works |
| --- | --- | --- |
| read any public GitHub asset | `ghless read owner/repo [path]`, `gh ...` | with a path: raw.githubusercontent.com, the fetch broker, then the forge API. Without a path: the README from the forge API, then api.github.com. `gh` is the real gh when its token works, else a read-only forge proxy with the same argv and output shapes (gh-forge). |
| copy and work on it | `ghless fetch owner/repo [dir]` | direct git, then git through the broker, then the source tarball. Tags the upstream commit `ghless/source-base`, opens branch `ghless/work`. |
| publish | `ghless publish [dir]` | github if a real token works, plus radicle, nostr (ngit/GRASP), arweave. Reports each lane. |
| temporary public web | `ghless serve` | static git index plus dumb-HTTP clone over `cfrs serve` (Cloudflare quick tunnel). |
| archive | `ghless archive [dir] [--wait]` | git bundles (upstream base, your changes, all), patch series, tarball, SHA-256 manifest, zip; Software Heritage and Wayback requests; ghre snapshot of upstream issues and PRs. |
| recover a lost account | `ghre ledger`, `ghre recover`, `ghre snapshot` | the ghre tool, unchanged. See `components/ghre/PLAYBOOK.md`. |
| bulk web archiving | `bulk-archive`, `spn2`, `megalodon`, ... | the bulk-archive kit. |
| what went where | `ghless status [--check]` | ledger of every publish; `--check` asks the Radicle seeds, the Nostr GRASP server, Arweave and the tunnel what they serve now |
| back up keys | `ghless identity export F` / `import F` | one 0600 archive of the radicle, nostr and arweave keys |
| preview | `ghless publish --dry-run` | which lanes are ready, nothing pushed |
| share the project | `ghless pack` | zip of this tree plus `.sha256`. |

When a new GitHub account exists, nothing changes for you: `gh` uses it,
`ghless doctor` shows `github account ok`, and `ghless publish` adds the
github lane first. The other lanes and the archive keep running regardless.

`gh` additionally answers `gh repo clone`, `gh release download` and `gh gist view` in forge mode (origin, then broker). `ghless COMMAND --help` prints each command's usage; `eval "$(ghless completion bash)"` adds completion.
Numbers and what was confirmed by running: `docs/MEASUREMENTS.md`.

## Install details

`./install [--core] [--no-tools] [--no-gh-shim] [--no-skill] [--no-path] [--uninstall] [--prefix DIR] [--bin DIR]`

1. System packages through the detected package manager (apt, apk, dnf, pacman): git, curl, python3, jq, unzip, zip, tar, gzip, xz, openssl, ssh, go. Missing ones only.
2. Pinned binaries into `$GHLESS_PREFIX/bin` (default `~/.local/share/ghless/bin`), each checked against a SHA-256 in `pins/pins.env`. A mismatch deletes the download and fails that tool. cfrs, Radicle, nak and ngit are release binaries; git-remote-arweave has no releases and is built with Go from a pinned commit.
3. Symlinks in `~/.local/bin` (ghless, gh, ghre, bulk-archive, spn2, ...). A link is never written over a real file; a pre-existing `~/.local/bin/gh` is left alone with a warning. `$GHLESS_PREFIX/env.sh` sets `PATH` and the ghless variables; a marked block in `~/.profile` and `~/.bashrc` sources it (a block with a missing end marker is left untouched).
4. The skill is linked into `~/.claude/skills/ghless` and `~/.agents/skills/ghless`.
5. `ghless doctor --brief` as a check.

`--core` installs packages and cfrs only. `--no-tools` downloads no binaries at all. Lanes can be added later with `ghless tools install radicle nak ngit arweave`.
Hermetic scope: the binaries are pinned and checksummed. The Go toolchain and modules used for git-remote-arweave are verified by Go (go.sum, checksum database), not pinned by ghless, and Go's state lives in `$GHLESS_PREFIX/go`. `tools install` re-checks the installed binary against the sha256 recorded at install time and reinstalls if it differs. Pins move only by editing `pins/pins.env`; `ghless update-check` shows what upstream has.

State: `$GHLESS_DATA` (default `~/.local/share/ghless/data`) holds identities
(`radicle/`, `nostr.key`, `arweave-wallet.json`, all private), `work/`,
`publish/` (staging repos), `archive/`, `pub/` and `published.tsv`, the ledger
of everything published.

## Hosts whose only egress is an HTTPS proxy

Radicle and ngit need raw TCP. `ghless publish` detects that (`direct_tcp`
fails), starts `cfrs agent up --warp` (WARP over MASQUE on TCP 443), runs ngit
inside `cfrs warp exec`, and maps Radicle seed ports to local listeners with
`cfrs warp forward`. `CFRS_STATE_DIR` is pinned to `$GHLESS_DATA/cfrs` for every
call so `up`, `exec` and `forward` always agree. Details and limits:
`docs/LANES.md`, `reference/cfrs-field-report/`.

## Tests

`sh tests/run.sh` runs offline checks (syntax, spec parsing, injection and name guards, gitweb build and clone, archive replay, pack with and without git, profile editing, ghre unit tests). `sh tests/run.sh --net` adds live fetch and read, refusal of a tampered pin, and a scratch-HOME install run twice.

## Layout

```
install  bin/ghless  libexec/*      installer, dispatcher, one script per command
pins/pins.env                        versions and SHA-256 of external binaries
components/gh-forge                  gh wrapper (unchanged except a doc line)
components/ghre                      recovery and snapshot tool (unchanged)
components/bulk-archive              web archiving scripts (one bug fixed)
reference/anonymous-git-hosting      survey, evidence and logs of every lane tried
reference/cfrs-field-report          cfrs 0.3.0-beta.12 field report and evidence
docs/  skill/ghless/SKILL.md    guides for people and agents
```
