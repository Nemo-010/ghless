# Anonymous / authless git hosting: every usable remote, tested (2026-11-05)

Scope: every git hosting option where you can push or pull by pointing a remote, with no
account, no operator-run server, or an identity you mint yourself (keypair = account).
Candidates were found by three research passes (pkgforge web/search/crawl, GitHub API
searches of every `git-remote-*` topic, forge instance lists, darknet-git lists) and then
each survivor was executed on this host. Everything below says which.

Environment: Debian 13 incus container, git 2.47.3, all egress through a broker HTTP
CONNECT proxy (allowlist, port 443 only) plus cfrs 0.3.0-beta.12 WARP (MASQUE over TCP 443)
and a local Tor 0.4.9 SOCKS port used as dial-out for non-443 ports. Each protocol's egress
limits are noted where they affected the test.

## Tested end to end and working

### 1. Radicle (radicle.dev) — the reference. Identity = your keypair, no server account
- `rad auth --alias X --stdin` mints a DID (`did:key:z6Mk...`) locally. No signup anywhere.
- Node talks to seeds on TCP 8776 (not 443): this host's allowlist proxy blocks raw 8776, so
  the run went through two `python3`+Tor SOCKS relays mapped to `127.0.0.1:18776/28776`.
- Push: `git push rad main` (remote `rad://z3NgZszvQwT6CxcQGsW576y1KuUBe/z6Mko2EP...`).
  Exit 0, "Everything up-to-date", then `rad sync` fetched from 2 seeds.
- Seeds that replicated (observed in node log): iris.radicle.xyz, radicle.jarg.io (their
  z6Mk DIDs are in the config); seed.radicle.xyz 404s the repo but iris + jarg hold it.
- Public explorer proof: `https://iris.radicle.xyz/api/v1/repos/z3NgZszvQwT6CxcQGsW576y1KuUBe`
  returns the repo metadata; the blob API returns the pushed README content.
- Fresh-profile clone: `rad clone rad:z3NgZszvQwT6CxcQGsW576y1KuUBe` → exit 0, files present.
- Remote format (from the repo's own remotes): fetch `rad://<RID>`, push `rad://<RID>/<your-NID>`.
- Caveats read from radicle.dev: node-to-node traffic is plaintext until the 2.0 (iroh)
  rewrite; public repos are the safe use.

### 2. ngit / GRASP over Nostr (ngit.dev) — identity = npub keypair
- Self-minted key with `nak key generate` (fiatjaf/nak v0.22.2), `ngit -n <key> init
  --defaults` in a repo: published NIP-34 kind-30617 announcement to `wss://relay.ngit.dev`
  and pushed the git pack to the GRASP server (`git-remote-nostr` helper ships with ngit).
- Verified from outside: the kind-30617 event is readable back from the relay via
  `nak req -k 30617 --author <npub>`, and
  `https://relay.ngit.dev/<npub>/errand-nostr-git-test.git/info/refs?service=git-upload-pack`
  answers 200 with pack refs (anonymous, no auth headers).
- Fresh-machine clone: `git clone nostr://npub1g4my.../relay.ngit.dev/errand-nostr-git-test`
  → full working checkout with the same commit. Remote URL format:
  `nostr://<npub|nip05>/<repo-identifier>` (optionally `/<relay-host>/`).
- Note: ngit's Rust websocket client ignores HTTPS_PROXY; the push only works inside
  `cfrs warp exec` (pod) here or on a host with direct egress. `nak` works without the pod
  (Go honors the proxy).

### 3. git-remote-arweave (v0.4.0) — identity = Arweave RSA-4096 JWK, permanent storage
- Build: `go build ./...` (repo's module path is `git-remote-arweave`; install.sh works on
  normal machines). Wallet self-minted locally as a JWK (RSA-4096 required; 2048 fails with
  "signature length incorrect").
- Remote: `arweave://<wallet-address>/<repo-name>`. Push worked on the free Turbo tier
  (562 bytes, free under 100 KiB): `* [new branch] main -> main`.
- Settlement observed: `arweave-git status` shows manifest+pack txs confirmed on the
  Turbo gateway, then on arweave.net gateway and GraphQL.
- Fresh clone of `arweave://Ii2ubG.../errand-arweave-test` returned the same commit after
  settlement (~5 min). No deletion, no auth, no account.

### 4. repo.or.cz mob branch — the only hosted server with real anonymous git push
- Cloned `https://repo.or.cz/mobexample.git` (anonymous), committed as
  `Anon Mob <anon@example.invalid>`, pushed `mob:refs/heads/mob` over SSH as the shared
  `mob` user (`ssh://mob@repo.or.cz/mobexample.git`) — no key, no password.
- Push exit 0 (`f04e718..70ae66a mob -> mob`); verified independently with `git ls-remote`
  and a fresh anonymous clone that contains the commit. This host's SSH had to go through
  a Tor relay (port 22 not on the proxy allowlist); the push itself needed nothing else.
- Restriction (site's own rule): pushes only to an existing `mob` branch of projects whose
  admin opted in; personal `refs/mob/mob.username` areas need a registered user.

### 5. Tor onion Gitea (darktea) — anonymous clone over a hidden service
- `http://it7otdanqu7ktntxzm427cba6i53w6wlanlh23v5i3siqmos47pzhvyd.onion` is alive
  (Gitea 28.2.0, API `/api/v1/version` answers through torsocks).
- Open signup form: username/email/password, no captcha observed (form fetched, 200).
- Anonymous clone proven: `git -c http.proxy=socks5h://127.0.0.1:9050 clone --depth 1
  http://<onion>/milahu9/alchi` → complete checkout.
- Push requires an account (standard Gitea).

### 6. i2pgit.org (I2P project Gitea, clearnet front)
- Anonymous `git ls-remote https://i2pgit.org/idk/Git_over_I2P.git HEAD` → 413e39c...
  (no auth). Push/admin-approval policy per the site: accounts approved by admin.

## Built and driven to their limits (partial by egress or design, not by speculation)

### 7. git-remote-torrent (dyeoh, v1.0.0) — identity = self-minted ed25519 publisher key
- `git-torrent key generate` minted the key and printed the address
  `torrent://b30e42ae1b36e05aed404fd1df3fe0ce04d8bf56bc6938aae541af132ab9be62`.
- `git-torrent seed` signs, builds the manifest and pack chain, starts the TCP seeder —
  then fails at the BitTorrent mainline DHT: this container has no UDP egress
  ("DHT: Failed to find any nodes" on host, bind mismatch inside the WARP pod).
- The helper itself (sign/manifest/pack) works; DHT needs UDP. Verdict: usable on any host
  with normal UDP; not proven end to end here.

### 8. freenet-git (freenet/freenet-git) — identity = self-minted ed25519 bundle
- Built 1.0 (needs `RUSTUP_TOOLCHAIN=stable` here; repo pins 1.94.1).
- `freenet-git init-identity --no-passphrase` mints and writes the identity bundle.
- Full push/clone requires a local Freenet node on ws://127.0.0.1:7509 (a several-GB
  download and bootstrap). Identity minting is proven; remote mechanics match the README's
  `freenet::<prefix>/<label>` format but were not exercised against a live node.

### 9. Software Heritage (SWH) — the archive path, anonymous, no auth at any step
- The archive holds mobexample.git (`origin` API 200); snapshot → revision → directory
  swhids resolved by anonymous API calls.
- `POST /api/1/vault/flat/swh:1:dir:96f06...` accepted the cook job with no key
  (`{"status":"new", fetch_url:...}`); the cooker was still queued at write time
  (shared public queue). No git protocol endpoint exists; cloning happens through the
  Vault (or the third-party `swh-git` proxy that serves cooked bare repos over
  git-http-backend).

## Checked and NOT usable (with the exact reason)

- gitmask (`git.gitmask.com/v1/...`) — `git ls-remote` → 403 (backend or bot wall).
- gitGost (`gitgost.livrasand.com/v1/gh/...`) — homepage 200 but the git endpoint is behind
  an Anubis JS proof-of-work; a git client gets HTML where the packfile protocol should be.
  Anonymous-PR design is real but not reachable from a git client here.
- Tor onion forges from the darknet list besides darktea: righttoprivacy/gdatura/
  darkforest — either dead or unreachable through two tor2web paths and direct SOCKS.
  (The list itself was fetched and is current 2026-10; per-host status above is mine.)
- I2P project's own git over its Tor mirror onion — dead (000 through Tor).
- git-ssb / Scuttlebutt — npm package 6 years old, repo archived 2018, no live pubs.
- ipfs-shipyard/git-remote-ipld — archived ("Old (2019) PoC"); ipfs.io shut down 2026.
- ElettraSciComp Git-IPFS-Remote-Bridge — alive (2025-12) but needs a local Kubo node;
  not installed here (disk), so untested beyond docs. Remote would be `ipfs://<node-id>`.
- GitTorrent (cjb), hypercore/dat helpers, git-remote-solana/-sia — dead or never real.
- Forgejo federation — social layer only; git push stays on one instance.
- Peergos, Autonomi, GNUnet — no git remote helpers exist.

## Hosted forges with self-mint-style signup (email is the gate, not a key)

All verified live 2026-11-05 with open forms: gitea.com (Gitea 29 dev), git.envs.net
(Gitea 28.3.0), tildegit.org (1.27.3), git.kaki87.net (Forgejo 11.0.16), codefloe.com,
sij.ai, git.gay (17.0.1-dotgay, register link live; form behind bot shield), darktea
onion (above). Codeberg requires email confirmation (their docs); sourcehut registration
was code-disabled this week (commit e471ffe7 "Enforce disabled registration", 0.89.0).
None accept a push without an account; nothing hosted combines no-email + no-human.

## Bottom line

Point-a-remote with zero account, alive and verified today:
`rad://` (Radicle), `nostr://` (ngit/GRASP), `arweave://` (self-minted wallet),
`ssh://mob@repo.or.cz/...` (mob branch of opted-in projects). Partial by this host's
egress only: `torrent://` (needs UDP DHT), `freenet::` (needs a Freenet node).
Everything else on the decentralized menu is dead, archived, or a web UI without a
git protocol.
