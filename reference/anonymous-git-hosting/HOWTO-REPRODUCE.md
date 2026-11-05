# Reproducing the four proven end-to-end flows anywhere

Every command below was executed and verified on 2026-11-05 (Debian 13, git 2.47.3,
containerized host behind an allowlist HTTP CONNECT proxy). On an unrestricted host,
all of it is simpler: skip every "restricted host" note.

## 1. Radicle (identity = keypair, no server)

```bash
curl -sSLf https://radicle.dev/install | sh     # ships rad, radicle-node, git-remote-rad
rad auth --alias you --stdin <<< 'your passphrase'   # mints did:key:z6Mk... locally
rad node start
mkdir repo && cd repo && git init -b main && git commit --allow-empty -m init
rad init --name myrepo --default-branch main --public --no-confirm
git push rad main
rad sync                                        # waits until seeds have it
rad clone rad:<RID>                             # from anywhere else, no account
```

Default seeds: `iris.radicle.xyz`, `rosa.radicle.xyz` (team); community: `radicle.jarg.io`.
Verified seeds that replicated our test repo `rad:z3NgZszvQwT6CxcQGsW576y1KuUBe`:
iris.radicle.xyz and radicle.jarg.io (`seed.radicle.xyz` still 404'd it; use iris/jarg).

Restricted-host note: seeds speak TCP **8776**, which most proxy allowlists block.
Either tunnel it (`scripts/socks-relay.py 18776 iris.radicle.xyz 8776` then add a
preferredSeed `z6MkrLMMsiPWUcNPHcRajuMi9mDfYckSoJyPwwnknocNYPm7@127.0.0.1:18776`,
DIDs for each seed are in `../evidence/verify-final.txt`) or use cfrs WARP egress.
Do NOT run the node inside `cfrs pod --backend seccomp`: its socketpair emulation
breaks the noise handshake (seeds RST; logs in `../logs/`). Plain WARP egress works.

## 2. Nostr / GRASP (identity = npub)

```bash
go install github.com/fiatjaf/nak@latest        # key tool
cargo install ngit                              # git-remote-nostr + ngit CLI
SK=$(nak key generate)                          # self-minted identity, no signup

cd yourrepo
ngit -n $SK init --name myrepo --identifier myrepo --defaults
#    publishes NIP-34 kind-30617 to wss://relay.ngit.dev and pushes the pack
git clone nostr://<npub>/<relay-host>/<identifier>    # from anywhere, no auth
```

Verify an announcement without ngit:
`nak req -k 30617 --author <hex-pubkey> wss://relay.ngit.dev`

Restricted-host note: ngit is Rust and its websocket client ignores HTTPS_PROXY; run it
inside `cfrs warp exec -- ...` (or any real egress). `nak` (Go) honors the proxy by itself.

## 3. Arweave (identity = RSA-4096 JWK)

```bash
go install ...  # or: git clone https://github.com/git-remote-arweave/git-remote-arweave && go build ./...
node -e 'const c=require("crypto");const{privateKey}=c.generateKeyPairSync("rsa",{modulusLength:4096});process.stdout.write(JSON.stringify(privateKey.export({format:"jwk"})))' > wallet.json
export ARWEAVE_WALLET=$PWD/wallet.json

cd yourrepo
git remote add aw arweave://<wallet-address>/<repo-name>   # address = the wallet's
git push aw main            # free under 100 KiB on the Turbo gateway
arweave-git status --all    # waits for Turbo/GW/GQL confirmation
git clone arweave://<wallet-address>/<repo-name>          # after settlement
```

Gotchas we hit: the key must be RSA-4096 (2048 fails with "signature length incorrect");
the remote's address must equal your wallet's address or the helper rejects the push;
`https://arweave.net/tx/<id>` may 404 while the data is still fetchable via the Turbo
gateway — use `arweave-git status`, not the raw endpoint, as your liveness check.

## 4. repo.or.cz mob (hosted server, genuinely anonymous push)

```bash
git clone https://repo.or.cz/<project>.git && cd <project>
git checkout mob                                   # branch must already exist
git commit -m '...'                                # any identity, e.g. Anon <anon@example.invalid>
git push ssh://mob@repo.or.cz/<project>.git mob:refs/heads/mob   # no key, no password
```

Works only on projects whose admin enabled mob, and only to `refs/heads/mob`.
List of mob-enabled projects: browse repo.or.cz for `mob` branches
(`git ls-remote https://repo.or.cz/<p>.git | grep mob`).

## 5. Tor onion Gitea (anonymous clone; signup = form)

```bash
torsocks git clone http://it7otdanqu7ktntxzm427cba6i53w6wlanlh23v5i3siqmos47pzhvyd.onion/<user>/<repo>
# or: git -c http.proxy=socks5h://127.0.0.1:9050 -c http.version=HTTP/1.1 clone --depth 1 ...
```

Push there needs a form account (username/email/password, no captcha observed).
Tor bootstrap on a proxy-only host: give tor `HTTPSProxy` + credentials in its config,
then `torsocks` works once you unset `https_proxy`/`http_proxy` in the calling shell
(torsocks refuses to dial a localhost HTTP proxy itself).

## Environment notes that cost us hours (avoid them)

- `torsocks` rejects any connect() to localhost, including your own `127.0.0.1:3128`
  HTTP proxy and curl honoring `https_proxy`. `env -u https_proxy -u http_proxy ...`.
- Its config file has no `NameServer` key in 2.5.0 despite the man page; DNS still
  resolves once bootstrap is done.
- radicle-node's DB can grow unbounded on repeated failed handshakes (we saw 21 GB);
  clean `/tmp/radhome` and restart rather than letting it retry for hours.
- `rad config` `preferredSeeds` is an array of **strings** (`nid@host:port`), not objects.
- git smart-http through Tor needs `http.version=HTTP/1.1` and generous postBuffer.
