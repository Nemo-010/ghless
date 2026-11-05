# Where errand-home itself is published

Published on 2026-11-05 by `errand publish` from this tree. Identities are throwaway and self-minted.
The github lane was skipped: no working GitHub token existed.

| lane | address |
| --- | --- |
| radicle | `rad:z3St7Pdm7Tsoe7uMpJYSMAnjLj1b9`, seeds iris.radicle.xyz and radicle.jarg.io held it when checked. Clone: `rad clone rad:z3St7Pdm7Tsoe7uMpJYSMAnjLj1b9` |
| nostr | `git clone nostr://npub1vzhvaumwpd0d6m5fh05cxqa5vqass0lw6jthk2s0enxtvjm7hlssu3m5d5/relay.ngit.dev/errand-home` (needs ngit's git-remote-nostr; behind a proxy-only host run it inside `cfrs warp exec`) |
| arweave | four parts, each `git clone arweave://7Gl3SLt5ps6EFNNnh6CavUcm4ZHPq65ir339LPF-quQ/errand-home-partN` for N in 1..4. Copy the top-level entries of every part into one directory. The full-history pack exceeds the free tier. |

A Cloudflare quick tunnel (`errand serve`) and the Software Heritage and Wayback archive requests (`errand archive`) are per-run
and recorded in `$ERRAND_DATA/published.tsv`, not here.
Later commits republish to the same addresses. The arweave parts only change when their content changes.
