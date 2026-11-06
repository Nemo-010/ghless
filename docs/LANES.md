# Publish lanes: what each does and what was observed

"Observed" means run on 2026-11-05 on a Debian 13 container whose direct egress is an HTTPS proxy
(port 443, domain allowlist), with ghless's own code. "Inferred" means read, not run.

| lane | identity | remote form | observed through `ghless publish` |
| --- | --- | --- | --- |
| github | your GitHub login | `https://github.com/<login>/<name>` | not run: no working token on this host (`github_authed` false, an account that was suspended refuses the token: `gh: Sorry. Your account was suspended (HTTP 403)`) |
| radicle | ed25519, `rad auth` with an empty passphrase | `rad:<RID>` | ok: iris.radicle.xyz and radicle.jarg.io answered HTTP 200 on `/api/v1/repos/<RID>` after the push; seed.radicle.xyz answered 404 |
| nostr | secp256k1 key minted by `libexec/nostr-key.py` (Python fallback; `nak` cannot run everywhere) | `nostr://<npub>/relay.ngit.dev/<identifier>` | ok: init and a second push both succeeded inside `cfrs warp exec` |
| arweave | RSA-4096 JWK minted with openssl | `arweave://<address>/<name>` | ok for a small repo. A 164 KiB repo was rejected: `turbo: upload returned 402` (x402 payment required), so the free tier is below that size. Larger projects are published as parts, see below. |

Radicle success means a public seed serves the repo. A push that stays local is reported as failed.

## Verification

After each lane pushes, `publish` asks the host what it serves now (GitHub API,
Radicle seed API, the GRASP server's git refs, the Arweave gateway transaction)
and compares it with the pushed commit. A match records `ok:verified` in the
ledger and the output; a timeout records plain `ok` (the push was accepted, the
index may lag). `--no-verify` skips the wait, `GHLESS_VERIFY_SECS` (default
240) bounds it. Arweave verification is transaction liveness: the manifest and
pack transactions of every part must answer HTTP 200 on arweave.net. The
ledger row for arweave carries every transaction id, so `ghless status
--check` can re-check them later without the local state.

## Arweave size limit

The free Turbo tier took a 0.5 KiB and a 0.75 KiB push and refused a 164 KiB one with HTTP 402 (all observed). When a push gets
402, `publish` runs `libexec/arweave-split.py`: top-level entries (large directories split into their children) are grouped
so each part packs under 75000 bytes (the tool's default, about 73 KiB), each part is pushed as `arweave://<address>/<name>-partK`, and every part carries `PARTS.md`
listing all parts, the source commit and how to reassemble (copy the top-level entries of every part into one directory).
Parts keep their own history between runs. Full history is on the radicle and nostr lanes. To publish whole with history, fund the wallet
(`$GHLESS_DATA/arweave-wallet.json`, address from `ghless identity`) and the plain push succeeds.

## Egress rules the lanes follow

- ngit ignores `HTTPS_PROXY` (observed, and in the survey): it runs under `cfrs warp exec` whenever `direct_tcp relay.ngit.dev 443` fails.
- Radicle seeds listen on non-443 ports (58776 for iris, 8776 for jarg, read from each seed's `/api/v1/node`). When dialing directly fails, each is forwarded with `cfrs warp forward --detach --name ghless-rad-N` and the node's `preferredSeeds` and `node.connect` point at `127.0.0.1:<port>`. Peers are set to `static` so the node does not spend its time dialing dead gossip addresses.
- The node runs on the host in the background via `detach_run` (in `libexec/common.sh`: `setsid` when available, else `nohup ... &`). Do not run it inside `cfrs pod --backend seccomp`: socketpair emulation breaks the noise handshake (survey logs in `reference/anonymous-git-hosting/logs/`, cfrs report item 2).
- Go programs (arweave helper, nak) honor the proxy and need no WARP.

## Lanes that exist in the survey and are not wired into `publish`

Each needs either a host or a resource this machine lacks, or accepts no new project. The evidence is in
`reference/anonymous-git-hosting/REPORT.md`.

- repo.or.cz `mob`: pushes only to the `mob` branch of a project whose admin opted in. Use it to contribute: `git push ssh://mob@repo.or.cz/<project>.git mob:refs/heads/mob`. Port 22 is blocked on proxy-only hosts; use `socks-relay` through Tor (`socks-relay 1822 repo.or.cz 22`).
- git-remote-torrent (needs UDP DHT), freenet-git (needs a Freenet node), Tor onion Gitea (push needs a form account), Software Heritage vault (read-only archive).
- Hosted forges with open signup (gitea.com, tildegit.org, git.envs.net, codefloe.com and others in the report) need an email account. Add one as an extra remote by hand: `git -C $GHLESS_DATA/publish/<name> remote add extra <url> && git push extra main`.

## Archive services

`ghless archive` asks Software Heritage (`POST /api/1/origin/save/git/url/<url>/`, anonymous) and Wayback (`spn2`, anonymous GET) and
snapshots upstream issues and PRs with `ghre snapshot`. A 200 from Software Heritage means the request was accepted;
whether and when the visit succeeds is checked with a GET on the same URL (not automated).
Limits from the bulk-archive kit apply: anonymous Wayback allows 5 captures per URL per day.
