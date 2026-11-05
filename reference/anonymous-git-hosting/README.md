# Anonymous / authless git hosting research (2026-11-05)

Standalone package: findings, raw evidence, reproducible commands. Everything runs from
any machine; see `HOWTO-REPRODUCE.md`. Original working directory layout preserved.

- `REPORT.md` — the full survey: what works, what is dead, exact reasons, all tested.
- `HOWTO-REPRODUCE.md` — the four proven flows + the restricted-host workarounds.
- `evidence/` — raw captured outputs from the session (not paraphrased).
- `logs/` — build logs and a radicle node log showing the failed pod handshake.
- `scripts/socks-relay.py` — TCP-over-SOCKS relay used to reach non-443 ports.

## One-paragraph summary

Four authless paths verified end to end by running them: Radicle (`rad://`, keypair
identity, seeds replicate in seconds), ngit/GRASP over Nostr (`nostr://`, npub identity,
relay.ngit.dev), git-remote-arweave (`arweave://`, self-generated RSA-4096 wallet, free
tier, permanent), and repo.or.cz's shared `mob` SSH account (true anonymous push to
opted-in projects' mob branches). Partial: git-remote-torrent (works; DHT needs UDP),
freenet-git (works; needs a local Freenet node), Software Heritage vault (anonymous cook
API; no git protocol endpoint). Everything else in the space (git-ssb, GitTorrent,
hypercore helpers, ipfs-shipyard, gitmask, gitGost) is dead, archived, or bot-walled.

## Test identities and artifacts left on the networks (all throwaway, by design)

- Radicle DID: `did:key:z6Mko2EP9VVBFB558bAL4AzcCAxtQWK8ipLHepnjff3dJnWx`
  (private key discarded with the container; repo stays public, that is the point)
- Nostr npub: `npub1g4myap5qmuqk5z87r3qu8gatwcrqp9g76l73c6uz0nnthnqzwsfsdcfkxm`
- Arweave address: `Ii2ubG-vJM6bfChJcEUfH_VLm9rFt9-cCaoT59Bq0ao`
- repo.or.cz mob commit: `70ae66a` on `mobexample.git#mob`
