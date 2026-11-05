#!/bin/sh
# bulk-archive.sh - fan a URL list out across the archive services in bin/.
#
# Usage:
#   bulk-archive.sh -f urls.txt [-s wayback,arquivo,megalodon,ghostarchive,archive-today,urlscan]
#                   [-w 8] [-o results.tsv]
#     -s list     services to use, comma-separated (default: all)
#     -w seconds  wait between submissions to each service (default 8)
#     -o file     TSV: url, service, status, capture-url
#
# Every underlying script also runs standalone; see README.md for the
# per-service limits and what was verified live vs. read from docs.

set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SERVICES="wayback,arquivo,megalodon,ghostarchive,archive-today,urlscan"
WAIT=8
OUT=""
FILE=""

while getopts "f:s:w:o:" opt 2>/dev/null; do
  case "$opt" in
    f) FILE="$OPTARG" ;;
    s) SERVICES="$OPTARG" ;;
    w) WAIT="$OPTARG" ;;
    o) OUT="$OPTARG" ;;
    *) echo "usage: $0 -f urls.txt [-s svc,svc] [-w s] [-o tsv]" >&2; exit 2 ;;
  esac
done

[ -n "$FILE" ] || { echo "need -f urls.txt" >&2; exit 2; }
LIST="$FILE"
if [ "$FILE" = "-" ]; then
  LIST=$(mktemp "${TMPDIR:-/tmp}/bulk-list.XXXXXX") || exit 1
  trap 'rm -f "$LIST"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  cat > "$LIST"
fi

tsv() { # url service status capture
  if [ -n "$OUT" ]; then printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$OUT"
  else printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4"; fi
}

run_one() { # script url service
  line=$(sh "$1" "$2" 2>/dev/null)
  url=$(printf '%s' "$line" | cut -f1)
  status=$(printf '%s' "$line" | cut -f2)
  cap=$(printf '%s' "$line" | cut -f3)
  tsv "$2" "$3" "$status" "$cap"
}

for svc in $(printf '%s' "$SERVICES" | tr ',' ' '); do
  case "$svc" in
    wayback)       script="$HERE/spn2.sh" ;;
    arquivo)       script="$HERE/arquivo-apn.sh" ;;
    megalodon)     script="$HERE/megalodon.sh" ;;
    ghostarchive)  script="$HERE/ghostarchive.sh" ;;
    archive-today) script="$HERE/archive-today.sh" ;;
    urlscan)       script="$HERE/urlscan.sh" ;;
    *) echo "unknown service: $svc" >&2; continue ;;
  esac
  grep -v -E '^\s*(#|$)' "$LIST" | while IFS= read -r u; do
    run_one "$script" "$u" "$svc"
    sleep "$WAIT"
  done || true
done
exit 0
