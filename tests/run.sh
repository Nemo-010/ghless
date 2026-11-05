#!/bin/sh
# Offline checks by default; --net adds live routes, pinned-binary refusal and a scratch install.
# Exit code 0 only when every check passed.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NET=0; [ "${1:-}" = "--net" ] && NET=1
T=$(mktemp -d "${TMPDIR:-/tmp}/errand-test.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok() { PASS=$((PASS + 1)); printf 'ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; }
check() { # check NAME CMD...
  n="$1"; shift
  if "$@" >"$T/out" 2>&1; then ok "$n"; else bad "$n"; sed 's/^/      /' "$T/out" | tail -8; fi
}
refuse() { # refuse NAME CMD... : passes only when the command exits non-zero
  n="$1"; shift
  if "$@" >"$T/out" 2>&1; then bad "$n (accepted bad input)"; else ok "$n"; fi
}

for f in "$ROOT/install" "$ROOT"/bin/errand "$ROOT"/libexec/* "$ROOT"/components/bulk-archive/bin/*.sh; do
  case "$f" in *.py|*.sh.orig) continue ;; esac
  head -n1 "$f" | grep -q 'sh' || continue
  check "syntax $(basename "$f")" sh -n "$f"
done
check "py_compile libexec" python3 -m py_compile "$ROOT/libexec/arweave-wallet.py" "$ROOT/libexec/gitweb-static.py"
check "ghre unit tests" sh -c "cd '$ROOT/components/ghre' && python3 -m unittest discover -s tests"

spec_case() { # spec expected "owner|repo|ref|path|url"
  got=$(sh -c ". '$ROOT/libexec/spec.sh'; parse_spec '$1' && echo \"\$S_OWNER|\$S_REPO|\$S_REF|\$S_PATH|\$S_URL\"")
  [ "$got" = "$2" ] || { echo "spec $1: got '$got' want '$2'"; return 1; }
}
check "spec owner/repo" spec_case pkgforge/soar "pkgforge|soar|||https://github.com/pkgforge/soar.git"
check "spec https url .git" spec_case https://github.com/pkgforge/soar.git "pkgforge|soar|||https://github.com/pkgforge/soar.git"
check "spec blob url" spec_case https://github.com/pkgforge/soar/blob/main/Cargo.toml "pkgforge|soar|main|Cargo.toml|https://github.com/pkgforge/soar.git"
check "spec git@ url" spec_case git@github.com:pkgforge/soar.git "pkgforge|soar|||https://github.com/pkgforge/soar.git"
check "spec host path" spec_case github.com/pkgforge/soar "pkgforge|soar|||https://github.com/pkgforge/soar.git"
refuse "spec rejects bare word" sh -c ". '$ROOT/libexec/spec.sh'; parse_spec nonsense"

# git fixture used by the offline checks
FX="$T/fx"; mkdir "$FX"
git -C "$FX" init -q -b main
printf '# fixture\nhello\n' > "$FX/README.md"; printf 'a\n' > "$FX/a.txt"
git -C "$FX" add -A; git -C "$FX" -c user.name=t -c user.email=t@t commit -qm base
git -C "$FX" tag errand/source-base
printf 'b\n' > "$FX/b.txt"; git -C "$FX" add -A; git -C "$FX" -c user.name=t -c user.email=t@t commit -qm change

export ERRAND_HOME="$ROOT" ERRAND_PREFIX="$T/prefix" ERRAND_DATA="$T/data"

pub_case() {
  python3 "$ROOT/libexec/gitweb-static.py" "$T/pub" fx="$FX" || return 1
  [ -f "$T/pub/index.html" ] && [ -f "$T/pub/fx/index.html" ] && [ -f "$T/pub/fx/view/README.md.html" ] || return 1
  port=$((20000 + $$ % 20000))
  (cd "$T/pub" && python3 -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1 & echo $! > "$T/hs.pid")
  i=0; until curl -s -o /dev/null "http://127.0.0.1:$port/" || [ $i -ge 20 ]; do sleep 0.2; i=$((i + 1)); done
  git clone -q "http://127.0.0.1:$port/fx.git" "$T/cl"; rc=$?
  kill "$(cat "$T/hs.pid")" 2>/dev/null
  [ $rc -eq 0 ] && [ "$(git -C "$T/cl" rev-parse HEAD)" = "$(git -C "$FX" rev-parse HEAD)" ]
}
check "gitweb index builds and clones over plain http" pub_case

arch_case() {
  "$ROOT/bin/errand" archive "$FX" --no-net >"$T/arch.out" 2>&1 || return 1
  z=$(tail -n1 "$T/arch.out"); [ -f "$z" ] || return 1
  d="${z%.zip}"
  (cd "$d" && sha256sum -c MANIFEST.sha256 >/dev/null) || return 1
  [ -f "$d/changes.patch" ] && [ -f "$d/source-base.bundle" ] && [ -f "$d/full.bundle" ] || return 1
  git clone -q "$d/source-base.bundle" "$T/base" 2>/dev/null; git -C "$T/base" checkout -q errand/source-base &&
    git -C "$T/base" -c user.name=t -c user.email=t@t am -q < "$d/changes.patch" || return 1
  [ "$(git -C "$T/base" rev-parse HEAD^{tree})" = "$(git -C "$FX" rev-parse HEAD^{tree})" ]
}
check "archive bundles replay to the same tree" arch_case

wallet_case() {
  addr=$("$ROOT/libexec/arweave-wallet.py" "$T/w.json") || return 1
  python3 - "$T/w.json" "$addr" <<'PY'
import base64, hashlib, json, sys
j = json.load(open(sys.argv[1]))
pad = lambda s: s + "=" * (-len(s) % 4)
n = base64.urlsafe_b64decode(pad(j["n"]))
assert len(n) == 512 and n[0] & 0x80, "modulus is not 4096 bits"
want = base64.urlsafe_b64encode(hashlib.sha256(n).digest()).rstrip(b"=").decode()
assert want == sys.argv[2], "address mismatch"
p, q = (int.from_bytes(base64.urlsafe_b64decode(pad(j[k])), "big") for k in ("p", "q"))
assert p * q == int.from_bytes(n, "big"), "p*q != n"
PY
}
check "arweave wallet is a valid RSA-4096 JWK" wallet_case
refuse "arweave wallet refuses to overwrite" "$ROOT/libexec/arweave-wallet.py" "$T/w.json"

pack_case() {
  mkdir "$T/packsrc" && (cd "$ROOT" && tar --exclude=.git -cf - .) | (cd "$T/packsrc" && tar -xf -)
  ERRAND_HOME="$T/packsrc" "$T/packsrc/bin/errand" pack "$T/p.zip" >/dev/null 2>&1 || return 1
  (cd "$T" && sha256sum -c p.zip.sha256 >/dev/null) || return 1
  mkdir "$T/unz" && (cd "$T/unz" && unzip -q ../p.zip) || return 1
  [ -x "$T/unz/errand-home/install" ] && [ -x "$T/unz/errand-home/bin/errand" ]
}
check "pack keeps execute bits and checksum" pack_case

bulk_case() {
  before=$(ls "${TMPDIR:-/tmp}"/bulk-list.* 2>/dev/null | wc -l)
  printf 'https://example.org/\n' | "$ROOT/components/bulk-archive/bin/bulk-archive.sh" -f - -s bogus -w 0 >/dev/null 2>&1
  after=$(ls "${TMPDIR:-/tmp}"/bulk-list.* 2>/dev/null | wc -l)
  [ "$before" = "$after" ]
}
check "bulk-archive stdin leaves no temp file" bulk_case

lane_refuses() { # a publish lane must not report ok without its tool
  PATH="/usr/bin:/bin" ERRAND_PREFIX="$T/empty" "$ROOT/bin/errand" publish "$FX" --name t --lanes radicle,nostr,arweave >"$T/pub.out" 2>&1
  [ $? -ne 0 ] && ! grep -q ' ok ' "$T/pub.out"
}
check "publish exits non-zero when no lane can run" lane_refuses

if [ "$NET" = 1 ]; then
  check "read README (live)" sh -c "'$ROOT/bin/errand' read octocat/Hello-World | head -c 20 | grep -q ."
  check "fetch live" sh -c "'$ROOT/bin/errand' fetch octocat/Hello-World '$T/hw' --depth 1 >/dev/null 2>&1 && git -C '$T/hw' rev-parse errand/source-base"

  pin_refusal() {
    cp -a "$ROOT" "$T/badroot" && sed -i 's/^NAK_SHA_x86_64=.*/NAK_SHA_x86_64="0000000000000000000000000000000000000000000000000000000000000000"/; s/^NAK_SHA_aarch64=.*/NAK_SHA_aarch64="0000000000000000000000000000000000000000000000000000000000000000"/' "$T/badroot/pins/pins.env"
    ERRAND_HOME="$T/badroot" ERRAND_PREFIX="$T/badprefix" "$T/badroot/libexec/tools" install nak >"$T/bad.out" 2>&1
    rc=$?
    [ $rc -ne 0 ] && [ ! -e "$T/badprefix/bin/nak" ] && grep -q 'sha256 mismatch' "$T/bad.out"
  }
  check "tampered pin is refused and leaves no binary" pin_refusal

  install_case() {
    H="$T/home"; mkdir -p "$H"
    HOME="$H" ERRAND_PREFIX="$H/p" ERRAND_DATA= "$ROOT/install" --core --no-skill >"$T/i1.out" 2>&1 || return 1
    HOME="$H" ERRAND_PREFIX="$H/p" ERRAND_DATA= "$ROOT/install" --core --no-skill >"$T/i2.out" 2>&1 || return 1
    grep -q 'already installed' "$T/i2.out" || return 1
    [ "$(grep -c '>>> errand-home' "$H/.profile")" = 1 ] || return 1
    [ -L "$H/.local/bin/errand" ] && [ -x "$H/p/bin/cfrs" ]
  }
  check "install --core twice in a scratch HOME is idempotent" install_case
fi

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
