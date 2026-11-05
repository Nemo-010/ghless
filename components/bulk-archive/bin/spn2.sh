#!/bin/sh
# spn2.sh - Internet Archive Save Page Now (SPN2) client, single or bulk.
#
# Verified 2026-11-05:
#   - anonymous GET https://web.archive.org/save/<url> still works: 302 to the
#     replay URL on success, 429 with x-rl:0 when the IP is held, and an HTML
#     "Sorry ... already captured 5 times today" page when the per-URL daily
#     cap is hit.
#   - POST https://web.archive.org/save without auth answers 401
#     {"message":"You need to be logged in to use Save Page Now."}
#   - authenticated POST uses "Authorization: LOW accesskey:secret"
#     (keys: https://archive.org/account/s3.php) and returns a job id you poll
#     at GET /save/status/<job>.
#
# Anonymous daily caps reported by SPN tooling: ~8,000 captures/IP/day
# (100,000 logged-in); per-URL cap 5/day. Keep concurrency low: 429s compound.
#
# Usage:
#   spn2.sh <url>                        one capture, anonymous
#   spn2.sh -a access:secret <url>       one capture, authenticated
#   spn2.sh [opts] -f urls.txt           bulk: reads URLs, one per line
#     -a access:secret   S3 keys (or env SPN2_AUTH). Never printed.
#     -s seconds         sleep between submissions (default 8)
#     -o file            TSV log (default stdout): url<TAB>status<TAB>capture
#     -d "k=v&k=v"       extra SPN2 POST options, e.g.
#                        "capture_outlinks=1&capture_screenshot=1"
#                        (outlinks/screenshot need auth)
#     -n                 do not send the default capture_all=on
#     -r seconds         max seconds to wait for a job to finish (default 600)
#   URL list on stdin also works:  spn2.sh -f - < urls.txt

set -u
AUTH="${SPN2_AUTH:-}"
SLEEP=8
OUT=""
EXTRA=""
SEND_ALL=1
MAXWAIT=600
FILE=""

while getopts "a:s:o:d:nr:f:" opt 2>/dev/null; do
  case "$opt" in
    a) AUTH="$OPTARG" ;;
    s) SLEEP="$OPTARG" ;;
    o) OUT="$OPTARG" ;;
    d) EXTRA="$OPTARG" ;;
    n) SEND_ALL=0 ;;
    r) MAXWAIT="$OPTARG" ;;
    f) FILE="$OPTARG" ;;
    *) echo "usage: $0 [-a access:secret] [-s s] [-o tsv] [-d opts] [-n] [-r s] [-f file|-] [url]" >&2; exit 2 ;;
  esac
done
shift $((OPTIND-1))

enc() { python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$1"; }

log() { # url status capture
  if [ -n "$OUT" ]; then printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$OUT"
  else printf '%s\t%s\t%s\n' "$1" "$2" "$3"; fi
}

warn() { printf 'spn2: %s\n' "$1" >&2; }

# --- one authenticated capture -------------------------------------------------
AUTHFILE=""
ensure_authfile() {
  [ -n "$AUTHFILE" ] && return 0
  AUTHFILE=$(mktemp "${TMPDIR:-/tmp}/spn2-auth.XXXXXX") || exit 1
  chmod 600 "$AUTHFILE"
  printf 'Authorization: LOW %s\n' "$AUTH" > "$AUTHFILE"
  trap 'rm -f "$AUTHFILE"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

capture_auth() {
  ensure_authfile
  u="$1"
  data="url=$(enc "$u")"
  [ "$SEND_ALL" = 1 ] && [ -z "$(printf '%s' "$EXTRA" | grep -o 'capture_all')" ] && data="$data&capture_all=on"
  [ -n "$EXTRA" ] && data="$data&$EXTRA"
  resp=$(curl -sS --max-time 60 -X POST -H "Accept: application/json" \
    -H "@$AUTHFILE" --data "$data" \
    -w '\n%{http_code}' "https://web.archive.org/save" 2>/dev/null)
  code=$(printf '%s' "$resp" | tail -n1); body=$(printf '%s' "$resp" | sed '$d')
  case "$code" in
    200)
      job=$(printf '%s' "$body" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("job_id",""))
except Exception: print("")')
      [ -z "$job" ] && { log "$u" "error" "no job_id in: $(printf '%s' "$body" | head -c 120)"; return; }
      i=0
      while [ "$i" -lt "$MAXWAIT" ]; do
        sleep 5; i=$((i+5))
        st=$(curl -sS --max-time 30 -H "Accept: application/json" \
          -H "@$AUTHFILE" \
          "https://web.archive.org/save/status/$job" 2>/dev/null)
        [ -z "$st" ] && continue
        status=$(printf '%s' "$st" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("status",""))
except Exception: print("")')
        case "$status" in
          success)
            ts=$(printf '%s' "$st" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(d.get("timestamp",""))')
            log "$u" "ok" "https://web.archive.org/web/$ts/$u"; return ;;
          error)
            msg=$(printf '%s' "$st" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(str(d.get("message",""))[:120])')
            log "$u" "error" "$msg"; return ;;
        esac
      done
      log "$u" "timeout" "job $job still running after ${MAXWAIT}s" ;;
    429)
      warn "429 held; sleeping 90s (raise -s, or fewer parallel jobs)"
      sleep 90; log "$u" "rate-limited" "" ;;
    401) log "$u" "error" "auth refused: $(printf '%s' "$body" | head -c 120)" ;;
    *)   log "$u" "error" "http $code: $(printf '%s' "$body" | head -c 120)" ;;
  esac
}

# --- one anonymous capture -----------------------------------------------------
capture_anon() {
  u="$1"
  hdr=$(curl -sS --max-time 90 -o /tmp/spn2.$$ -D - \
    "https://web.archive.org/save/$u" 2>/dev/null | tr -d '\r')
  loc=$(printf '%s' "$hdr" | grep -i '^location:' | head -n1 | cut -d' ' -f2-)
  ts=$(printf '%s' "$hdr" | grep -i '^x-ts:' | head -n1 | cut -d' ' -f2)
  case "$ts" in
    302)
      [ -n "$loc" ] && log "$u" "ok(existing-or-new)" "$loc" \
                  || log "$u" "ok" "" ;;
    429)
      warn "429 held; sleeping 90s"
      sleep 90; log "$u" "rate-limited" "" ;;
    200)
      # HTML answer: either the daily per-URL cap page or an "already captured" notice
      if grep -q "already captured 5 times today" /tmp/spn2.$$ 2>/dev/null; then
        log "$u" "daily-url-cap" "5 captures of this URL today; try tomorrow"
      else
        log "$u" "already-captured" "$(printf '%s' "$hdr" | grep -i '^x-location:' | head -n1 | cut -d' ' -f2-)"
      fi ;;
    *) log "$u" "http-$ts" "" ;;
  esac
  rm -f /tmp/spn2.$$
}

# --- main ----------------------------------------------------------------------
if [ -z "$FILE" ]; then
  [ $# -lt 1 ] && { echo "need a URL or -f file" >&2; exit 2; }
  if [ -n "$AUTH" ]; then capture_auth "$1"; else capture_anon "$1"; fi
  exit 0
fi

[ "$FILE" = "-" ] && LIST=/tmp/spn2-list.$$ || LIST="$FILE"
[ "$FILE" = "-" ] && cat > "$LIST"
grep -v -E '^\s*(#|$)' "$LIST" | while IFS= read -r u; do
  [ -n "$AUTH" ] && capture_auth "$u" || capture_anon "$u"
  sleep "$SLEEP"
done
[ "$FILE" = "-" ] && rm -f "$LIST"
