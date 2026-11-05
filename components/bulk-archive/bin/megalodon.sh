#!/bin/sh
# megalodon.sh - submit URLs to Megalodon.jp (web gyo, Japanese archive).
#
# Verified 2026-11-05:
#   1. GET https://megalodon.jp/?url=<u>  (the gyo.tc/<url> bookmarklet form
#      lands here) returns the confirmation page. It carries a hidden
#      csrf_token and shows a per-IP quota counter ("0/60" = 60 captures/hour).
#   2. POST https://megalodon.jp/pc/get_simple/decide with csrf_token and url
#      starts the capture; the result page carries the new gyo URL
#      (megalodon.jp/pc/get/<stamp>/<ip-hash>/<path>).
#   No account. YouTube/streaming and login-walled pages are unsupported.
#
# Usage:
#   megalodon.sh <url>               submit
#     -o file    TSV log
#     -f file    bulk, one URL per line (sleeps to stay under 60/hour)

set -u
OUT=""

while getopts "o:f:" opt 2>/dev/null; do
  case "$opt" in
    o) OUT="$OPTARG" ;;
    f) FILE="$OPTARG" ;;
    *) echo "usage: $0 [-o tsv] [-f file|-] [url]" >&2; exit 2 ;;
  esac
done
shift $((OPTIND-1))

enc() { python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$1"; }

log() {
  if [ -n "$OUT" ]; then printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$OUT"
  else printf '%s\t%s\t%s\n' "$1" "$2" "$3"; fi
}

submit() {
  u="$1"
  jar=/tmp/mega.$$
  # step 1: confirmation page (holds csrf token + session cookie)
  code=$(curl -sS --max-time 60 -c "$jar" -o /tmp/mega-p.$$ -w '%{http_code}' \
    "https://megalodon.jp/?url=$(enc "$u")" 2>/dev/null)
  if [ "$code" != "200" ]; then log "$u" "http-$code" "confirm page"; rm -f "$jar" /tmp/mega-p.$$; return; fi
  tok=$(grep -o -E 'name="csrf_token" value="[0-9a-f]+"' /tmp/mega-p.$$ | head -n1 | grep -o -E '[0-9a-f]{40,}')
  if [ -z "$tok" ]; then
    log "$u" "no-csrf" "$(head -c 120 /tmp/mega-p.$$)"
    rm -f "$jar" /tmp/mega-p.$$; return
  fi
  # step 2: decide. The service redirects to http:// (plain); force https on the
  # redirect target since some networks block :80 (this sandbox does).
  loc=$(curl -sS --max-time 120 -b "$jar" -X POST \
    --data-urlencode "csrf_token=$tok" --data-urlencode "url=$u" \
    -o /tmp/mega-r.$$ -D - -w '%{http_code}' \
    "https://megalodon.jp/pc/get_simple/decide" 2>/dev/null \
    | grep -i -E '^(HTTP|location)' | tr -d '\r')
  code=$(printf '%s\n' "$loc" | grep -i '^HTTP' | tail -n1 | awk '{print $2}')
  next=$(printf '%s\n' "$loc" | grep -i '^location:' | tail -n1 | cut -d' ' -f2)
  case "$code" in
    302)
      case "$next" in
        http://*) next="https://${next#http://}" ;;
      esac
      curl -sS --max-time 120 -b "$jar" -L --max-redirs 5 -o /tmp/mega-f.$$ "$next" 2>/dev/null
      gyo=$(grep -o -E 'https?://megalodon\.jp/pc/get/[0-9]+-[0-9]+/[^"< ]+' /tmp/mega-f.$$ | head -n1)
      [ -n "$gyo" ] && log "$u" "ok" "$gyo" || log "$u" "accepted" "no gyo URL parsed; check $next"
      ;;
    200)
      # decide answered inline: either the finished page or the bot-block page
      gyo=$(grep -o -E 'https?://megalodon\.jp/pc/get/[0-9]+-[0-9]+/[^"< ]+' /tmp/mega-r.$$ | head -n1)
      if [ -n "$gyo" ]; then log "$u" "ok" "$gyo"
      elif grep -q "取得出来ませんでした" /tmp/mega-r.$$ 2>/dev/null; then
        log "$u" "bot-blocked" "service flags scripted requests; needs a browser or slower pacing"
      else
        log "$u" "unknown" "200 without gyo URL; check confirm page for quota counter"
      fi ;;
    *) log "$u" "http-$code" "$(printf '%s\n' "$loc" | tail -n1 | head -c 120)" ;;
  esac
  rm -f "$jar" /tmp/mega-p.$$ /tmp/mega-r.$$ /tmp/mega-f.$$
}

if [ -z "${FILE:-}" ]; then
  [ $# -lt 1 ] && { echo "need a URL or -f file" >&2; exit 2; }
  submit "$1"
  exit 0
fi
[ "$FILE" = "-" ] && LIST=/tmp/mega-list.$$ && cat > "$LIST" || LIST="$FILE"
# 60/hour per IP: one submission per 65 s keeps a full hour clear
grep -v -E '^\s*(#|$)' "$LIST" | while IFS= read -r u; do
  submit "$u"
  sleep 65
done
[ "${FILE:-}" = "-" ] && rm -f /tmp/mega-list.$$
