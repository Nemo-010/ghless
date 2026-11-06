#!/bin/sh
# ghostarchive.sh - submit URLs to ghostarchive.org.
#
# Verified 2026-11-05 from this network:
#   - the web form is <form action="/archive2" method="POST"> with one field,
#     name="archive".
#   - POST without a Cloudflare clearance cookie answers 403 "Just a moment..."
#     (managed challenge). The save interface also normally requires
#     JavaScript, so unattended bulk use needs a real browser profile that has
#     passed the challenge once; plain curl gets the gate. Replays are fine
#     over plain HTTP.
#   - replay/lookup needs no challenge:
#       https://ghostarchive.org/archive/<YYYYMMDD>/<url>   (by date)
#       https://ghostarchive.org/archive/<id>               (short id)
#   - page cap 50 MB (video 100 MB, up to 360p).
#
# Usage:
#   ghostarchive.sh <url>            submit (needs CF clearance, see below)
#   ghostarchive.sh -c <url>         check whether a capture exists today
#     -o file    TSV log
#
# To submit from a shell, first pass the challenge in Firefox/Chromium, then
# copy the cf_clearance cookie into the environment:
#   GHOST_CF_COOKIE='<cf_clearance value>' GHOST_UA='<same UA as the browser>' \
#     ghostarchive.sh -f urls.txt
# The cookie is IP+UA bound; a mismatched UA re-triggers the challenge.

set -u
CHECK=0
OUT=""
UA="${GHOST_UA:-Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36}"

while getopts "co:f:" opt 2>/dev/null; do
  case "$opt" in
    c) CHECK=1 ;;
    o) OUT="$OPTARG" ;;
    f) FILE="$OPTARG" ;;
    *) echo "usage: $0 [-c] [-o tsv] [-f file|-] [url]" >&2; exit 2 ;;
  esac
done
shift $((OPTIND-1))

log() {
  if [ -n "$OUT" ]; then printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$OUT"
  else printf '%s\t%s\t%s\n' "$1" "$2" "$3"; fi
}

cookiefile=""
if [ -n "${GHOST_CF_COOKIE:-}" ]; then
  cookiefile=$(mktemp "${TMPDIR:-/tmp}/ga-cookie.XXXXXX") || exit 1
  chmod 600 "$cookiefile"
  printf 'Cookie: cf_clearance=%s\n' "$GHOST_CF_COOKIE" > "$cookiefile"
fi

check() {
  u="$1"
  today=$(date +%Y%m%d)
  # follow the redirect: an existing capture lands on /archive/<short-id>
  # regardless of the date asked for; 404 only when nothing exists at all
  final=$(curl -sS -A "$UA" ${cookiefile:+-H "@$cookiefile"} --max-time 60 -o /tmp/ga.$$ -L --max-redirs 3 \
    -w '%{url_effective} %{http_code}' "https://ghostarchive.org/archive/$today/$u" 2>/dev/null)
  code=${final##* }
  final=${final% *}
  if [ "$code" = 404 ]; then log "$u" "none" ""
  elif printf '%s' "$final" | grep -q -E '/archive/[A-Za-z0-9]{5}$'; then log "$u" "exists" "$final"
  else log "$u" "http-$code" "$(printf '%s' "$final" | head -c 120)"; fi
  rm -f /tmp/ga.$$
}

submit() {
  u="$1"
  body=$(curl -sS -A "$UA" ${cookiefile:+-H "@$cookiefile"} --max-time 120 -X POST \
    --data-urlencode "archive=$u" -o /tmp/ga.$$ -w '%{http_code}' \
    "https://ghostarchive.org/archive2" 2>/dev/null)
  case "$body" in
    200)
      id=$(grep -o -E '/archive/[A-Za-z0-9]{5}' /tmp/ga.$$ | head -n1)
      [ -n "$id" ] && log "$u" "ok" "https://ghostarchive.org$id" \
                 || log "$u" "accepted" "no id in response; check https://ghostarchive.org/archive/$(date +%Y%m%d)/$u" ;;
    403)
      if grep -q "Just a moment" /tmp/ga.$$ 2>/dev/null; then
        log "$u" "cloudflare" "pass the challenge in a browser, export GHOST_CF_COOKIE + GHOST_UA"
      else
        log "$u" "forbidden" ""
      fi ;;
    *) log "$u" "http-$body" "$(head -c 120 /tmp/ga.$$ 2>/dev/null)" ;;
  esac
  rm -f /tmp/ga.$$
}

if [ -z "${FILE:-}" ]; then
  [ $# -lt 1 ] && { echo "need a URL or -f file" >&2; exit 2; }
  if [ "$CHECK" = 1 ]; then check "$1"; else submit "$1"; fi
  exit 0
fi
[ "$FILE" = "-" ] && LIST=/tmp/ga-list.$$ && cat > "$LIST" || LIST="$FILE"
grep -v -E '^\s*(#|$)' "$LIST" | while IFS= read -r u; do
  if [ "$CHECK" = 1 ]; then check "$u"; else submit "$u"; fi
  sleep 3
done
[ "${FILE:-}" = "-" ] && rm -f /tmp/ga-list.$$
