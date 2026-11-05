# Bulk web-archiving kit

Companion scripts to web.pkgforge.dev's `archive.web.pkgforge.dev`, which saves
one page at a time through Save Page Now. For bulk work you need the services
themselves, and two of them expose no API at all - those get web-form scripts.

Everything here was probed live on 2026-11-05 from a datacenter sandbox
(outbound TLS, no IPv6, no browser). Each claim below says which it is:
**[live]** probed today, **[docs]** from the service's own docs or ArchiveTeam
wiki, not reproducible from this network.

## The services that accept jobs

| service | how jobs are accepted | auth | bulk path | evidence |
| --- | --- | --- | --- | --- |
| Internet Archive SPN2 | `GET /save/<url>` (anon) or `POST /save` (auth) + job polling | none, or free-account S3 keys (`LOW access:secret`) | script + IA's own Google Sheets service (5,000 URLs/sheet, ~30,000/day) | [live] anon 302→replay; 429 `x-rl:0` hold; per-URL daily-5 "Sorry" page; [live] unauth POST = 401 JSON |
| Arquivo.pt ArchivePageNow | `POST /services/archivepagenow` with `url` + two consent fields | none | script (fire-and-forget, no job id) | [live] 200 accepted |
| Megalodon.jp (ウェブ魚拓) | two-step: GET `/?url=` for csrf, POST `/pc/get_simple/decide` | none | script; **60 captures/hour/IP** shown on the confirm page | [live] full flow; redirect uses plain `http://`, which some networks block - script rewrites to https |
| urlscan.io | `POST /api/v1/scan/` with `api-key` header | free account key | script; rate-limit headers honoured; day quota resets 00:00 UTC | [live] 401 without key; keyless search works |
| Ghostarchive | web form `POST /archive2`, field `archive` | none (but Cloudflare managed challenge) | script; needs `cf_clearance` cookie + matching UA from a real browser pass | [live] 403 "Just a moment" for plain curl; page cap 50 MB [docs] |
| archive.today | **no API**; `GET /submit/?url=<u>&anyway=1` follows through to the short-id page | none | script; reCAPTCHA gates datacenter IPs [docs], aliases rotate | [docs] ArchiveTeam wiki; [live] all aliases resolve but TLS to 104.36.80.x is blackholed from this network - the script is built from the wiki + the pp-archive-is CLI, unverified here |
| Perma.cc | REST API, key required | paid/free limited | out of scope; Cloudflare-gated API from here [live: 403 challenge] |
| ArchiveBot (ArchiveTeam) | IRC `!a <url>` in #archivebot (hackint), voice/+ or op required | n/a | whole-site crawls into WARC→Wayback; human-in-the-loop, not scriptable anonymously | [docs] |
| IA Wayback-GSheets | Google Sheet of URLs → archive.org/services/wayback-gsheets | IA + Google account | the sanctioned bulk lane: 5,000/sheet, ~30,000/day [docs] | helper: github.com/HussamHallak/SaveURLsToGoogleSheets |

## Not accepting jobs any more

- **Conifer (Rhizome/Webrecorder)**: no new accounts since 2025-12-15
  ("twilight"); captures halt May 2026; read-only tombstone after. Use
  Browsertrix (hosted app.browsertrix.com, API with account) or self-host
  browsertrix-crawler instead. [docs]
- **WebCite**: dead. **archive.st**: captcha-walled, dropped by upstream tools.

## Self-hosted (no service to ask)

- **ArchiveBox** — `archivebox add < urls.txt`; local archive of everything.
- **browsertrix-crawler** — WARC-grade crawling in Docker.
Both need hosts this sandbox does not have; they are the offline endgame,
not the networked one.

## Scripts

```
bin/spn2.sh            IA Save Page Now: anon GET or authed POST+poll, bulk, TSV log
bin/arquivo-apn.sh     Arquivo.pt ArchivePageNow submit + timemap lookup
bin/megalodon.sh       Megalodon two-step (csrf) submit, 65s pacing
bin/ghostarchive.sh    Ghostarchive /archive2 submit (-c for lookup), CF cookie support
bin/archive-today.sh   archive.today submit via /submit/?url=&anyway=1, alias rotation
bin/urlscan.sh         urlscan.io API submit + keyless search
bin/bulk-archive.sh    fan a URL list across all of the above
```

Common usage: `<script> <url>` for one, `-f urls.txt` for bulk, `-o log.tsv`
for a TSV of `url / status / capture-url`. `-c`/`-l` on the scripts that
support it means lookup-only (never submit).

```
bin/bulk-archive.sh -f examples/urls.txt -s wayback,arquivo,megalodon -o results.tsv
```

Keys go in the environment, never in files: `SPN2_AUTH` (access:secret),
`URLSCAN_API_KEY`, `GHOST_CF_COOKIE` + `GHOST_UA`.

## Realistic throughput, from the limits above

| lane | sustained rate |
| --- | --- |
| SPN2 anonymous | seconds apart per URL, but per-URL cap 5/day and IP holds on bursts; ~8,000/IP/day claimed by tooling [docs] |
| SPN2 authenticated | 100,000/day account cap [docs]; parallel jobs ≤ ~15 before 429s [docs] |
| IA GSheets | ~30,000/day, hands off [docs] |
| Arquivo.pt | no published quota observed; pace politely |
| Megalodon | 60/hour/IP (hard, shown in UI) [live] |
| urlscan | free quotas per minute/hour/day; read the X-Rate-Limit headers [live] |
| Ghostarchive | no published quota; gated per-browser by the CF challenge [live] |
| archive.today | unpredictable; reCAPTCHA on datacenter IPs [docs] |

## Verified failure modes to handle

- SPN2: `429` with `x-rl: 0` (IP hold - back off hard), HTML 200 with
  "already captured 5 times today" (per-URL cap, not an error), `401` JSON on
  unauth POST [all live].
- web.pkgforge.dev archive broker: its `POST /save` legs go into `429 held`
  windows with `resetAt` after bursts - the broker is not a bulk lane either;
  use these scripts for volume [live].
- Megalodon redirects the capture flow over plain `http://`; on networks that
  block :80 the script rewrites to `https://` [live].
- archive.today "ditch" pages: failed captures leave "Not Found (yet?)" with
  no title; the submission URL removes itself from tab history, which is why
  scripted submission is the reliable route [docs].

## What this kit is not

No wayback-machine-spn-scripts replacement (that bash project is the
reference implementation for recursive outlink capture - overcast07 on
GitHub); this kit keeps per-URL fan-out simple. No headless browser here:
archive.today and Ghostarchive submissions are the two places where a real
browser profile beats curl, and the scripts degrade to honest status lines
(`captcha`, `cloudflare`) when they hit that wall instead of lying about
success.
