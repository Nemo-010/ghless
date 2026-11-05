# Shared shell helpers. Sourced by install and every libexec/ command.
# shellcheck shell=sh

: "${GHLESS_HOME:?GHLESS_HOME must be set before sourcing common.sh}"
if [ -z "${HOME:-}" ]; then
  HOME=$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)
  [ -n "$HOME" ] || { echo "ghless: error: HOME is not set" >&2; exit 1; }
  export HOME
fi
# XDG_DATA_HOME is honoured only inside $HOME, so a different HOME is always an isolated install.
case "${XDG_DATA_HOME:-}" in "$HOME"/*) _xdg="$XDG_DATA_HOME" ;; *) _xdg="$HOME/.local/share" ;; esac
GHLESS_PREFIX="${GHLESS_PREFIX:-$_xdg/ghless}"
GHLESS_DATA="${GHLESS_DATA:-$GHLESS_PREFIX/data}"
GHLESS_BIN="${GHLESS_BIN:-$HOME/.local/bin}"
GHLESS_WORK="${GHLESS_WORK:-$GHLESS_DATA/work}"
export GHLESS_HOME GHLESS_PREFIX GHLESS_DATA GHLESS_BIN GHLESS_WORK
PATH="$GHLESS_PREFIX/bin:$PATH"
export PATH

# shellcheck disable=SC1091
. "$GHLESS_HOME/pins/pins.env"

say() { printf 'ghless: %s\n' "$*" >&2; }
warn() { printf 'ghless: warning: %s\n' "$*" >&2; }
die() { printf 'ghless: error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

arch() {
  case "${GHLESS_ARCH:-$(uname -m)}" in
    x86_64|amd64) echo x86_64 ;;
    aarch64|arm64) echo aarch64 ;;
    armv7l|armv7) echo armv7 ;;
    i686|i386) echo i686 ;;
    riscv64) echo riscv64 ;;
    *) echo "${GHLESS_ARCH:-$(uname -m)}" ;;
  esac
}

# pin_value PREFIX -> value of PREFIX_<arch> from pins.env (empty when unpinned).
pin_value() {
  eval "printf '%s' \"\${${1}_$(arch):-}\""
}

# Direct URL first, then the same URL through the fetch broker.
# fetch_to URL DEST
fetch_to() {
  _url="$1"; _dest="$2"; _tmp="$_dest.part.$$"
  for _try in "$_url" "$BROKER/$_url"; do
    if curl -fsSL --retry 2 --connect-timeout 15 -m 900 -o "$_tmp" "$_try" 2>/dev/null; then
      mv -f "$_tmp" "$_dest"; return 0
    fi
  done
  rm -f "$_tmp"
  return 1
}

# download_pinned URL SHA DEST: refuses a file whose sha256 differs from SHA.
download_pinned() {
  _url="$1"; _sha="$2"; _dest="$3"
  [ -n "$_sha" ] || { warn "no pinned sha256 for $(basename "$_url") on $(arch)"; return 2; }
  fetch_to "$_url" "$_dest" || { warn "cannot download $_url"; return 1; }
  _got=$(sha256sum "$_dest" | cut -d' ' -f1)
  if [ "$_got" != "$_sha" ]; then
    rm -f "$_dest"
    warn "sha256 mismatch for $(basename "$_url"): want $_sha got $_got"
    return 3
  fi
}

file_sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

# Default git environment: never prompt, fail fast on dead links.
git_safe() {
  GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=true \
    git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 "$@"
}

# github_authed: true only when a real, working GitHub token answers /user.
# `gh auth status` is not used: it exits 0 for a suspended account.
github_authed() {
  have gh || return 1
  GH_FORGE_MODE=real gh api user --jq .login >/dev/null 2>&1
}

# abspath PATH: absolute form of a path that may not exist yet.
abspath() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$PWD" "$1" ;;
  esac
}

# valid_name NAME: a name safe to use as a directory and remote identifier.
valid_name() {
  case "$1" in
    ""|.|..|-*|.*|*[!A-Za-z0-9._-]*) return 1 ;;
  esac
  return 0
}

# export_tree SRC DEST: copy SRC to DEST without .git or secrets.
#  - a git checkout: tracked files only (git ls-files), so untracked or ignored
#    keys can never travel;
#  - ghless itself, not a checkout: only the shipped top-level entries;
#  - any other directory: everything except common secret file names.
export_tree() {
  mkdir -p "$2"
  if git -C "$1" rev-parse --show-toplevel >/dev/null 2>&1 && [ "$(git -C "$1" rev-parse --show-toplevel)" = "$(cd "$1" && pwd)" ]; then
    git -C "$1" ls-files -z | (cd "$1" && tar --null -T - -cf -) | (cd "$2" && tar -xf -)
  elif [ "$(cd "$1" && pwd)" = "$(cd "$GHLESS_HOME" && pwd)" ]; then
    for _e in install README.md VERSION .gitignore bin libexec components reference docs skill pins tests; do
      [ -e "$1/$_e" ] && (cd "$1" && tar --exclude=__pycache__ --exclude='*.pyc' --exclude=work \
        --exclude='*.key' --exclude='*wallet*.json' --exclude='.env' --exclude='.env.*' -cf - "$_e") | (cd "$2" && tar -xf -)
    done
  else
    (cd "$1" && tar --exclude=.git --exclude='*.key' --exclude='*.pem' --exclude='.env' --exclude='.env.*' \
      --exclude='id_rsa*' --exclude='id_ed25519*' --exclude='*wallet*.json' -cf - .) | (cd "$2" && tar -xf -)
  fi
}

# zip_dir DIR OUT: zip(1) when present, else python's zipfile. OUT must be absolute.
zip_dir() {
  _d="$1"; _o="$2"
  rm -f "$_o"
  if have zip; then
    (cd "$(dirname "$_d")" && zip -qr "$_o" "$(basename "$_d")")
  else
    python3 - "$_d" "$_o" <<'PY'
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
base = os.path.dirname(src.rstrip("/"))
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        for f in files:
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, base))
PY
  fi
}

# ledger_add LANE PROJECT URL STATUS
ledger_add() {
  mkdir -p "$GHLESS_DATA"
  printf '%s\t%s\t%s\t%s\t%s\n' "$(now_utc)" "$1" "$2" "$3" "$4" >> "$GHLESS_DATA/published.tsv"
}

# direct_tcp HOST PORT: true when this host can open the TCP connection itself.
direct_tcp() {
  python3 - "$1" "$2" <<'PY' 2>/dev/null
import socket, sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), 6).close()
except Exception:
    sys.exit(1)
PY
}

export CFRS_STATE_DIR="${CFRS_STATE_DIR:-$GHLESS_DATA/cfrs}"

# free_port: a TCP port nothing listens on right now (forwards are host-global).
free_port() {
  python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'
}

# warp_up: start the detached WARP proxy (idempotent). Needed on hosts whose
# only egress is an HTTPS proxy: ngit and radicle speak raw TCP.
warp_up() {
  have cfrs || return 1
  mkdir -p "$CFRS_STATE_DIR"
  [ -S "$CFRS_STATE_DIR/run/warp.sock" ] && cfrs ps 2>/dev/null | grep -q '^warp .*ready' && return 0
  cfrs agent up --warp >/dev/null 2>&1
}

# net_run CMD...: run through WARP when this host cannot reach the open internet
# by itself, otherwise run directly. GHLESS_WARP=0 forces direct, =1 forces WARP.
net_run() {
  case "${GHLESS_WARP:-auto}" in
    0) "$@"; return ;;
    1) warp_up && { cfrs warp exec -- "$@"; return; } ;;
    *) if ! direct_tcp relay.ngit.dev 443; then
         warp_up && { cfrs warp exec -- "$@"; return; }
       fi ;;
  esac
  "$@"
}

# nostr_keygen / nostr_npub use the Python implementation (checked against nak on
# random keys in tests), so nak never writes ~/.config/nak and also runs on musl.
nostr_keygen() { python3 "$GHLESS_HOME/libexec/nostr-key.py" gen; }
nostr_npub() { python3 "$GHLESS_HOME/libexec/nostr-key.py" npub < "$1"; }

# detach_run LOG CMD...: start CMD in its own session, output to LOG, surviving
# this shell. `setsid -f` is not portable (busybox lacks -f), so the command is
# backgrounded and started with plain setsid, or nohup when setsid is missing.
detach_run() {
  _log="$1"; shift
  if have setsid; then setsid "$@" > "$_log" 2>&1 < /dev/null &
  else nohup "$@" > "$_log" 2>&1 < /dev/null &
  fi
}
