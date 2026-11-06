#!/bin/sh
# arquivo-apn.sh - Arquivo.pt ArchivePageNow submitter.
#
# Verified 2026-11-05:
#   the form at https://arquivo.pt/services/archivepagenow is
#   <form action="/services/archivepagenow?l=pt" method="POST"> with fields
#   url, consent-protection, consent-processing (the two consent checkboxes
#   are required). A curl POST of all three answers 200 and the capture is
#   integrated into the Arquivo.pt corpus afterwards (pywb-based service, no
#   job id and no redirect to a replay URL - it is fire-and-forget).
#   Verification is therefore by lookup, not by job status:
#     https://arquivo.pt/wayback/timemap/link/<url>   Memento timemap
#
# Usage:
#   arquivo-apn.sh <url>             submit
#   arquivo-apn.sh -c <url>          list captures (timemap)
#     -o file    TSV log
#     -f file    bulk submit / lookup, one URL per line

set -u
CHECK=0
OUT=""

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

check() {
  u="$1"
  code=$(curl -sS --max-time 60 -o /tmp/apn.$$ -w '%{http_code}' \
    "https://arquivo.pt/wayback/timemap/link/$u" 2>/dev/null)
  case "$code" in
    200) n=$(grep -c '^<' /tmp/apn.$$ 2>/dev/null)
         first=$(grep -o -E 'https?://arquivo.pt/wayback/[0-9]{14}' /tmp/apn.$$ | head -n1)
         log "$u" "exists($n)" "$first" ;;
    404) log "$u" "none" "" ;;
    *)   log "$u" "http-$code" "" ;;
  esac
  rm -f /tmp/apn.$$
}

submit() {
  u="$1"
  code=$(curl -sS --max-time 90 -X POST -o /tmp/apn.$$ -w '%{http_code}' \
    "https://arquivo.pt/services/archivepagenow?l=en" \
    --data-urlencode "url=$u" \
    --data-urlencode "consent-protection=on" \
    --data-urlencode "consent-processing=on" 2>/dev/null)
  case "$code" in
    200) log "$u" "accepted" "verify later: https://arquivo.pt/wayback/timemap/link/$u" ;;
    *)   log "$u" "http-$code" "$(head -c 120 /tmp/apn.$$ 2>/dev/null)" ;;
  esac
  rm -f /tmp/apn.$$
}

if [ -z "${FILE:-}" ]; then
  [ $# -lt 1 ] && { echo "need a URL or -f file" >&2; exit 2; }
  if [ "$CHECK" = 1 ]; then check "$1"; else submit "$1"; fi
  exit 0
fi
[ "$FILE" = "-" ] && LIST=/tmp/apn-list.$$ && cat > "$LIST" || LIST="$FILE"
grep -v -E '^\s*(#|$)' "$LIST" | while IFS= read -r u; do
  if [ "$CHECK" = 1 ]; then check "$u"; else submit "$u"; fi
  sleep 3
done
[ "${FILE:-}" = "-" ] && rm -f /tmp/apn-list.$$
