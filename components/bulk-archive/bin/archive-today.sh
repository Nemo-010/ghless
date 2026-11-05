#!/bin/sh
# archive-today.sh - submit URLs to archive.today, which has no API.
#
# Mechanism (from ArchiveTeam wiki + the pp-archive-is CLI, unverified from
# this network on 2026-11-05: all archive.today aliases resolve here but TLS
# to 104.36.80.x is blackholed from datacenter space):
#   submit:  GET https://<alias>/submit/?url=<u>&anyway=1   (anyway=1 skips the
#            "already archived, resave?" interstitial)
#   success: redirect chain lands on https://<alias>/<short-id>  (4-5 alnum)
#   ditch:   failed capture shows "Not Found (yet?)" with no title; the job
#            was dropped - retry the URL at least once, later
#   lookup:  GET https://<alias>/newest/<u>        latest capture (200 or 404)
#   caveat:  datacenter/VPN IPs routinely get constant reCAPTCHAs; residential
#            lines are fine. Aliases rotate; .ph/.today/.is/.md/.li are current.
#
# Usage:
#   archive-today.sh <url>                one submit
#   archive-today.sh -f urls.txt          bulk (3s between submissions)
#     -a alias      one of archive.ph archive.today archive.is archive.md archive.li
#     -l            lookup only: print latest capture or nothing, never submit
#     -o file       TSV log
#     -s seconds    sleep between submissions (default 3)
#     -A agent      user agent (default: a plain Chrome string)

set -u
ALIAS=""
LOOKUP=0
OUT=""
SLEEP=3
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

while getopts "a:los:A:f:" opt 2>/dev/null; do
  case "$opt" in
    a) ALIAS="$OPTARG" ;;
    l) LOOKUP=1 ;;
    o) OUT="$OPTARG" ;;
    s) SLEEP="$OPTARG" ;;
    A) UA="$OPTARG" ;;
    f) FILE="$OPTARG" ;;
    *) echo "usage: $0 [-a alias] [-l] [-o tsv] [-s s] [-A ua] [-f file|-] [url]" >&2; exit 2 ;;
  esac
done
shift $((OPTIND-1))

[ -n "$ALIAS" ] || ALIAS="archive.ph"
enc() { python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$1"; }

log() {
  if [ -n "$OUT" ]; then printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$OUT"
  else printf '%s\t%s\t%s\n' "$1" "$2" "$3"; fi
}

lookup() {
  u="$1"
  body=$(curl -sS -A "$UA" --max-time 60 -o /tmp/at.$$ -w '%{http_code}' \
    "https://$ALIAS/newest/$u" 2>/dev/null)
  case "$body" in
    200)
      cap=$(grep -o -E "https://$ALIAS/[0-9]{4}-?[0-9]{2}-?[0-9]{2}-[0-9]{2}:?[0-9]{2}:?[0-9]{2}|https://$ALIAS/[A-Za-z0-9]{4,6}" /tmp/at.$$ | head -n1)
      log "$u" "exists" "${cap:-https://$ALIAS/newest/$u}" ;;
    404) log "$u" "none" "" ;;
    *)   log "$u" "http-$body" "" ;;
  esac
  rm -f /tmp/at.$$
}

submit() {
  u="$1"
  # follow the whole chain; the final URL carries the short id
  final=$(curl -sS -A "$UA" --max-time 120 -L --max-redirs 30 -o /tmp/at.$$ \
    -w '%{url_effective}' "https://$ALIAS/submit/?url=$(enc "$u")&anyway=1" 2>/dev/null)
  code=""
  case "$final" in
    *".city"*) code="cf-block" ;;
  esac
  id=$(printf '%s' "$final" | grep -o -E "https://$ALIAS/[A-Za-z0-9]{4,6}$" | head -n1)
  if [ -n "$id" ]; then
    log "$u" "ok" "$id"
  elif grep -q -i -E "not found \(yet|time out|network error" /tmp/at.$$ 2>/dev/null; then
    log "$u" "ditched" "retry later"
  elif grep -q -i -E "captcha|recaptcha|unusual traffic" /tmp/at.$$ 2>/dev/null; then
    log "$u" "captcha" "solve one in a browser, then rerun"
    warn "reCAPTCHA gate hit; bulk submits from this IP will keep failing"
  else
    log "$u" "unknown" "$(printf '%s' "$final" | head -c 120)"
  fi
  rm -f /tmp/at.$$
}

if [ -z "${FILE:-}" ]; then
  [ $# -lt 1 ] && { echo "need a URL or -f file" >&2; exit 2; }
  [ "$LOOKUP" = 1 ] && lookup "$1" || submit "$1"
  exit 0
fi
[ "$FILE" = "-" ] && LIST=/tmp/at-list.$$ && cat > "$LIST" || LIST="$FILE"
grep -v -E '^\s*(#|$)' "$LIST" | while IFS= read -r u; do
  [ "$LOOKUP" = 1 ] && lookup "$u" || submit "$u"
  sleep "$SLEEP"
done
[ "${FILE:-}" = "-" ] && rm -f /tmp/at-list.$$
