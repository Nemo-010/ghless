#!/bin/sh
# urlscan.sh - submit URLs to urlscan.io as public scans.
#
# Verified 2026-11-05:
#   - POST /api/v1/scan/ without a key answers 401 "No API key supplied";
#     a free account (https://urlscan.io) gives an API key. Submissions are
#     quota- and rate-limited; the API answers X-Rate-Limit-* headers and
#     429 past them (fixed windows: minute/hour/day, day resets 00:00 UTC).
#   - search is keyless: GET /api/v1/search/?q=domain:<d> worked anonymously.
#   A scan's page (DOM, resources, screenshot) is a durable third-party
#   capture; result URLs look like https://urlscan.io/result/<uuid>/.
#
# Usage:
#   urlscan.sh <url>                 submit one (env URLSCAN_API_KEY required)
#   urlscan.sh -c <url>              keyless search for past scans of the URL
#     -v unlisted     submit unlisted instead of public
#     -o file         TSV log
#     -f file         bulk submit, one URL per line

set -u
KEY="${URLSCAN_API_KEY:-}"
CHECK=0
VIS="public"
OUT=""

while getopts "cv:o:f:" opt 2>/dev/null; do
  case "$opt" in
    c) CHECK=1 ;;
    v) VIS="$OPTARG" ;;
    o) OUT="$OPTARG" ;;
    f) FILE="$OPTARG" ;;
    *) echo "usage: $0 [-c] [-v public|unlisted] [-o tsv] [-f file|-] [url]" >&2; exit 2 ;;
  esac
done
shift $((OPTIND-1))

log() {
  if [ -n "$OUT" ]; then printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$OUT"
  else printf '%s\t%s\t%s\n' "$1" "$2" "$3"; fi
}

check() {
  u="$1"
  enc=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "page.url:\"$u\"")
  curl -sS --max-time 60 "https://urlscan.io/api/v1/search/?q=$enc&size=3" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); r=d.get("results",[])
    print("\n".join(x["task"]["time"]+" "+x["_id"] for x in r) or "none")
except Exception as e: print("search-failed")' \
    | while IFS= read -r line; do log "$u" "scan" "https://urlscan.io/result/${line##* }/ (at ${line%% *})"; done
}

submit() {
  u="$1"
  if [ -z "$KEY" ]; then log "$u" "no-key" "set URLSCAN_API_KEY (free account)"; return; fi
  resp=$(curl -sS --max-time 60 -X POST -H "api-key: $KEY" -H "content-type: application/json" \
    -d "{\"url\":$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$u"),\"visibility\":\"$VIS\"}" \
    -D /tmp/us.$$ "https://urlscan.io/api/v1/scan/" 2>/dev/null)
  code=$(grep -i '^HTTP' /tmp/us.$$ | tail -n1 | tr -d '\r' | awk '{print $2}')
  rem=$(grep -i '^X-Rate-Limit-Remaining:' /tmp/us.$$ | tail -n1 | tr -d '\r' | awk '{print $2}')
  case "$code" in
    200)
      uuid=$(printf '%s' "$resp" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("result","").rstrip("/").split("/")[-1])
except Exception: print("")')
      [ -n "$uuid" ] && log "$u" "ok" "https://urlscan.io/result/$uuid/" \
                  || log "$u" "accepted" "$resp"
      [ -n "$rem" ] && [ "$rem" -le 1 ] 2>/dev/null && {
        reset=$(grep -i '^X-Rate-Limit-Reset-After:' /tmp/us.$$ | tail -n1 | tr -d '\r' | awk '{print $2}')
        echo "urlscan: last submission in this window; sleeping ${reset:-60}s" >&2
        sleep "${reset:-60}"
      } ;;
    429) warn429=$(grep -i '^X-Rate-Limit-Reset-After:' /tmp/us.$$ | tail -n1 | tr -d '\r' | awk '{print $2}')
         log "$u" "rate-limited" "reset in ${warn429:-?}s" ;;
    401) log "$u" "bad-key" "" ;;
    *)   log "$u" "http-$code" "$(printf '%s' "$resp" | head -c 120)" ;;
  esac
  rm -f /tmp/us.$$
}

if [ -z "${FILE:-}" ]; then
  [ $# -lt 1 ] && { echo "need a URL or -f file" >&2; exit 2; }
  [ "$CHECK" = 1 ] && check "$1" || submit "$1"
  exit 0
fi
[ "$FILE" = "-" ] && LIST=/tmp/us-list.$$ && cat > "$LIST" || LIST="$FILE"
grep -v -E '^\s*(#|$)' "$LIST" | while IFS= read -r u; do
  [ "$CHECK" = 1 ] && check "$u" || submit "$u"
  sleep 3
done
[ "${FILE:-}" = "-" ] && rm -f /tmp/us-list.$$
