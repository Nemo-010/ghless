# Measurements

All numbers: Debian 13 container, Linux 6.12.113, x86_64, 8 cores, egress through an HTTPS proxy,
2026-11-05, 5 runs per command, `sh tests/bench.sh 5` (min / median / max wall-clock ms). Network commands move with the network.

| command | before | after | what changed |
| --- | --- | --- | --- |
| `ghless version` | 7 / 8 / 10 | 6 / 7 / 9 | none |
| `ghless doctor --brief` | 24 / 27 / 32 | 23 / 23 / 25 | none |
| `ghless doctor` (network probes) | 4899 / 5819 / 6413 | 2175 / 2404 / 3012 | probes run in parallel |
| `ghless read owner/repo` (README) | 496 / 725 / 2031 | 124 / 136 / 145 | raw.githubusercontent HEAD first, no API call |
| `ghless read owner/repo README` | 81 / 127 / 653 | 36 / 38 / 39 | same |
| `ghless fetch --depth 1` | 893 / 934 / 995 | 904 / 921 / 939 | unchanged (git-bound) |
| `gh repo view` forge mode, cold cache | 409 / 2376 / 3099 | 656 / 866 / 972 | gh-forge unchanged; variance is the proxy |
| `gh repo view` auto mode, warm cache | 307 / 354 / 1171 | 320 / 566 / 1338 | unchanged |
| `curl api.github.com` (reference) | 45 / 81 / 93 | 48 / 84 / 405 | n/a |

Install, one run each, scratch HOME, from nothing: 40.9 s sequential tools, 34.9 s with parallel tool installs
(the Go build of git-remote-arweave dominates); second run 0.24 to 0.47 s. The Go module cache (about 580 MB with the auto-fetched
Go 1.26.1 toolchain) is now deleted after the build; the prefix is 158 MB.

## Confirmed by running (not read)

- Pinned sha256 for cfrs and pod-netns on x86_64, aarch64, armv7, i686, riscv64, and radicle, nak, ngit on aarch64, all match the downloaded release files.
  The riscv64 cfrs asset is named `riscv64gc`; the first pin logic used the wrong name and would have failed there. Fixed.
- Under qemu-user (emulated, so it proves the binaries start, not their speed): aarch64 cfrs, rad, nak, ngit; armv7 and riscv64 cfrs report their version.
- Alpine 3.24.3 (musl, busybox sh) in a chroot: install, tools, read, fetch, 48 tests, identity init, a real publish to nostr and arweave and radicle.
  Found and fixed there: `setsid -f` does not exist in busybox (radicle node never started), `nak` is a glibc binary and cannot run (Python key fallback added),
  the Go module cache was left read-only in the prefix.
- GitHub lane: exercised against a stub `gh` and a local bare remote (create, push HEAD, refuse a diverged history). Not run against github.com: no token exists.
- Software Heritage: save requests for github.com origins from earlier runs show `save_task_status: succeeded`, `visit_status: full`.
  A request for a trycloudflare.com URL stayed `pending` with no task created.

## Not measured
- Real GitHub push. Real aarch64 hardware speed. Install time on a machine with a cold apt cache.
