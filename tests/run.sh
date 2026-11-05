#!/bin/sh
# Offline checks by default; --net adds live routes, pinned-binary refusal and a scratch install.
# Exit code 0 only when every check passed.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NET=0; [ "${1:-}" = "--net" ] && NET=1
T=$(mktemp -d "${TMPDIR:-/tmp}/ghless-test.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0; SKIP=0
IS_GIT=0; [ "$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null)" = "$ROOT" ] && IS_GIT=1
ok() { PASS=$((PASS + 1)); printf 'ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; }
check() { # check NAME CMD...
  n="$1"; shift
  if "$@" >"$T/out" 2>&1; then ok "$n"; else bad "$n"; sed 's/^/      /' "$T/out" | tail -8; fi
}
gitcheck() { # gitcheck NAME CMD...: needs ROOT to be a git checkout, skipped in an unzipped tree
  if [ "$IS_GIT" = 1 ]; then check "$@"; else SKIP=$((SKIP + 1)); printf 'skip  %s (not a git checkout)\n' "$1"; fi
}
refuse() { # refuse NAME CMD... : passes only when the command exits non-zero
  n="$1"; shift
  if "$@" >"$T/out" 2>&1; then bad "$n (accepted bad input)"; else ok "$n"; fi
}

for f in "$ROOT/install" "$ROOT"/bin/ghless "$ROOT"/libexec/* "$ROOT"/components/bulk-archive/bin/*.sh; do
  case "$f" in *.py|*.sh.orig) continue ;; esac
  head -n1 "$f" | grep -q 'sh' || continue
  check "syntax $(basename "$f")" sh -n "$f"
done
check "py_compile libexec" python3 -m py_compile "$ROOT/libexec/arweave-wallet.py" "$ROOT/libexec/gitweb-static.py"
check "gh-forge transfer tests" python3 "$ROOT/tests/test_transfer.py"
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
git -C "$FX" tag ghless/source-base
printf 'b\n' > "$FX/b.txt"; git -C "$FX" add -A; git -C "$FX" -c user.name=t -c user.email=t@t commit -qm change

export GHLESS_HOME="$ROOT" GHLESS_PREFIX="$T/prefix" GHLESS_DATA="$T/data"

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
  "$ROOT/bin/ghless" archive "$FX" --no-net >"$T/arch.out" 2>&1 || return 1
  z=$(tail -n1 "$T/arch.out"); [ -f "$z" ] || return 1
  d="${z%.zip}"
  (cd "$d" && sha256sum -c MANIFEST.sha256 >/dev/null) || return 1
  [ -f "$d/changes.patch" ] && [ -f "$d/source-base.bundle" ] && [ -f "$d/full.bundle" ] || return 1
  git clone -q "$d/source-base.bundle" "$T/base" 2>/dev/null; git -C "$T/base" checkout -q ghless/source-base &&
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
  GHLESS_HOME="$T/packsrc" "$T/packsrc/bin/ghless" pack "$T/p.zip" >/dev/null 2>&1 || return 1
  (cd "$T" && sha256sum -c p.zip.sha256 >/dev/null) || return 1
  mkdir "$T/unz" && (cd "$T/unz" && unzip -q ../p.zip) || return 1
  [ -x "$T/unz/ghless/install" ] && [ -x "$T/unz/ghless/bin/ghless" ]
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
  PATH="/usr/bin:/bin" GHLESS_PREFIX="$T/empty" "$ROOT/bin/ghless" publish "$FX" --name t --lanes radicle,nostr,arweave >"$T/pub.out" 2>&1
  [ $? -ne 0 ] && ! grep -q ' ok ' "$T/pub.out" && [ "$(grep -c 'not installed' "$T/pub.out")" -ge 3 ]
}
check "publish exits non-zero when no lane can run" lane_refuses

split_case() {
  "$ROOT/libexec/arweave-split.py" "$ROOT" "$T/parts" t TESTADDR 30000 >"$T/parts.out" 2>&1 || return 1
  [ "$(wc -l < "$T/parts.out")" -ge 3 ] || return 1
  mkdir "$T/joined"
  for pn in $(cat "$T/parts.out"); do
    [ "$(git -C "$T/parts/$pn" archive HEAD | gzip -9 | wc -c)" -le 31000 ] || { echo "$pn too big"; return 1; }
    git -C "$T/parts/$pn" archive HEAD | tar -x -C "$T/joined" || return 1
  done
  for f in install bin/ghless libexec/publish components/gh-forge/ghforge/jqish.py reference/cfrs-field-report/cfrs-field-report.md; do
    git -C "$ROOT" show "HEAD:$f" | cmp -s - "$T/joined/$f" || { echo "$f differs after reassembly"; return 1; }
  done
}
gitcheck "arweave split parts reassemble to the same files" split_case

fetch_pos_case() {
  "$ROOT/bin/ghless" fetch "file://$FX" "$T/fp" >/dev/null 2>&1 || return 1
  [ "$(git -C "$T/fp" config ghless.route)" = direct ] &&
  [ "$(git -C "$T/fp" rev-parse ghless/source-base)" = "$(git -C "$FX" rev-parse HEAD)" ] &&
  [ "$(git -C "$T/fp" branch --show-current)" = ghless/work ] &&
  "$ROOT/bin/ghless" fetch "file://$FX" "$T/fp" >/dev/null 2>&1
}
check "fetch clones, tags ghless/source-base, opens ghless/work, re-run is a no-op" fetch_pos_case

offline_install_case() {
  H3="$T/home3"; mkdir -p "$H3/.local/bin"
  printf 'top=1\n# >>> ghless >>>\nold\n# <<< ghless <<<\nmid=2\n# >>> ghless >>>\nstale=3\nlast=4\n' > "$H3/.profile"
  printf '# mine' > "$H3/.zshrc"
  ln -s /bin/true "$H3/.local/bin/gh"
  HOME="$H3" GHLESS_PREFIX="$H3/p" "$ROOT/install" --no-tools --no-skill >"$T/oi.out" 2>&1
  [ $? -ne 0 ] || { echo "install exited 0 despite an unterminated block"; return 1; }
  grep -q '^last=4$' "$H3/.profile" && grep -q '^stale=3$' "$H3/.profile" || { echo "profile lines were lost"; return 1; }
  [ "$(readlink "$H3/.local/bin/gh")" = /bin/true ] || { echo "foreign gh link was replaced"; return 1; }
  HOME="$H3" GHLESS_PREFIX="$H3/p" "$ROOT/install" --no-tools --no-skill --uninstall >/dev/null 2>&1
  H4="$T/home4"; mkdir -p "$H4"; printf '# mine' > "$H4/.profile"
  HOME="$H4" GHLESS_PREFIX="$H4/p" "$ROOT/install" --no-tools --no-skill >/dev/null 2>&1
  HOME="$H4" GHLESS_PREFIX="$H4/p" "$ROOT/install" --no-tools --no-skill >/dev/null 2>&1
  sh -n "$H4/.profile" && [ "$(grep -c '>>> ghless' "$H4/.profile")" = 1 ] && grep -q '^# mine$' "$H4/.profile" &&
  HOME="$H4" GHLESS_PREFIX="$H4/p" "$ROOT/install" --no-tools --no-skill --uninstall >/dev/null 2>&1 &&
  [ "$(cat "$H4/.profile")" = "# mine" ] && [ ! -e "$H4/.local/bin/ghless" ]
}
check "install: profile edits are safe, idempotent and reversible; foreign links kept" offline_install_case

pack_ignored_case() {
  git clone -q "$ROOT" "$T/pk2" 2>/dev/null || return 1
  cp -a "$ROOT/." "$T/pk2/" 2>/dev/null; rm -rf "$T/pk2/.git"; git clone -q "$ROOT" "$T/pk3" 2>/dev/null
  (cd "$T/pk3" && git ls-files -z | head -c0)
  mkdir -p "$T/pk3/components/ghre/work"; echo K > "$T/pk3/components/ghre/work/nostr.key"; echo K > "$T/pk3/tests/arweave-wallet.json"
  GHLESS_HOME="$T/pk3" "$T/pk3/bin/ghless" pack "$T/pk3.zip" >/dev/null 2>&1 || return 1
  ! unzip -l "$T/pk3.zip" | grep -qE 'nostr\.key|arweave-wallet\.json'
  [ $? -eq 0 ] || return 1
  GHLESS_HOME="$T/pk2" "$T/pk2/bin/ghless" pack "$T/pk2.zip" >/dev/null 2>&1 || return 1
  ! unzip -l "$T/pk2.zip" | grep -qE 'nostr\.key|arweave-wallet\.json'
}
gitcheck "pack never ships untracked or ignored key files (git and non-git trees)" pack_ignored_case

binary_case() {
  B="$T/binrepo"; mkdir "$B"; git -C "$B" init -q -b main; head -c 2000 /dev/urandom > "$B/b.dat"; git -C "$B" add -A
  git -C "$B" -c user.name=t -c user.email=t@t commit -qm b
  cp "$B/b.dat" "$T/b.orig"; head -c 2000 /dev/urandom > "$B/b.dat"; cp "$B/b.dat" "$T/b.new"
  "$ROOT/bin/ghless" archive "$B" --no-net >"$T/bin.out" 2>&1 || return 1
  d=$(ls -d "$GHLESS_DATA"/archive/binrepo-*/ | head -n1)
  git clone -q "$d/full.bundle" "$T/binrest" 2>/dev/null && git -C "$T/binrest" apply "$d/uncommitted.patch" && cmp -s "$T/binrest/b.dat" "$T/b.new"
}
check "archive keeps uncommitted binary changes" binary_case

gitweb_rerun_case() {
  SM="$T/smrepo"; mkdir "$SM"; git -C "$SM" init -q -b main; echo x > "$SM/f"; git -C "$SM" add f
  git -C "$SM" update-index --add --cacheinfo 160000,1111111111111111111111111111111111111111,sub
  git -C "$SM" -c user.name=t -c user.email=t@t commit -qm sm
  python3 "$ROOT/libexec/gitweb-static.py" "$T/pub2" sm="$SM" >/dev/null 2>&1
  python3 "$ROOT/libexec/gitweb-static.py" "$T/pub2" sm="$SM" >/dev/null 2>&1
  [ -f "$T/pub2/sm/index.html" ] || return 1
  ! grep -rIl "$T" "$T/pub2/sm.git" 2>/dev/null | grep -q .
}
check "gitweb tolerates submodule entries, re-runs, and leaks no local path" gitweb_rerun_case

github_lane_case() {
  mkdir -p "$T/ghstub" "$T/ghremote/me"
  cat > "$T/ghstub/gh" <<STUB
#!/bin/sh
case "\$*" in
  "api user --jq .login") echo me; exit 0 ;;
  "repo view me/"*) [ -d "$T/ghremote/me/\${3#me/}.git" ] ; exit \$? ;;
  "repo create me/"*) n="\${3#me/}"; git init -q --bare "$T/ghremote/me/\$n.git"; exit 0 ;;
esac
exit 1
STUB
  chmod +x "$T/ghstub/gh"
  export GHLESS_GITHUB_URL="file://$T/ghremote"
  out=$(PATH="$T/ghstub:$PATH" "$ROOT/bin/ghless" publish "$FX" --name ghp --lanes github 2>&1) || { echo "$out"; return 1; }
  [ "$(git --git-dir="$T/ghremote/me/ghp.git" rev-parse main)" = "$(git -C "$FX" rev-parse HEAD)" ] || { echo "remote head differs"; return 1; }
  # a rewritten history must be rejected, never force-pushed
  FXR="$T/fxr"; git clone -q "$FX" "$FXR"; git -C "$FXR" reset -q --hard HEAD~1
  git -C "$FXR" -c user.name=t -c user.email=t@t commit -q --allow-empty -m diverge
  mkdir -p "$GHLESS_DATA/publish/ghp" && printf '%s' "$FXR" > "$GHLESS_DATA/publish/ghp/.git/ghless-source"
  out=$(PATH="$T/ghstub:$PATH" "$ROOT/bin/ghless" publish "$FXR" --name ghp --lanes github 2>&1) && { echo "diverged push was accepted"; return 1; }
  printf '%s' "$out" | grep -q 'failed' && [ "$(git --git-dir="$T/ghremote/me/ghp.git" rev-parse main)" = "$(git -C "$FX" rev-parse HEAD)" ]
}
check "github lane: creates the repo, pushes HEAD, refuses a diverged push (stub gh, file remote)" github_lane_case
unset GHLESS_GITHUB_URL

identity_case() {
  GHLESS_DATA="$T/idA" "$ROOT/bin/ghless" identity init >/dev/null 2>&1 || return 1
  a=$(GHLESS_DATA="$T/idA" "$ROOT/bin/ghless" identity show | grep -E '^(nostr|arweave)')
  GHLESS_DATA="$T/idA" "$ROOT/bin/ghless" identity export "$T/ids.tgz" >/dev/null 2>&1 || return 1
  GHLESS_DATA="$T/idA" "$ROOT/bin/ghless" identity export "$T/ids.tgz" >/dev/null 2>&1 && return 1
  [ "$(stat -c %a "$T/ids.tgz")" = 600 ] || return 1
  GHLESS_DATA="$T/idB" "$ROOT/bin/ghless" identity import "$T/ids.tgz" >/dev/null 2>&1 || return 1
  b=$(GHLESS_DATA="$T/idB" "$ROOT/bin/ghless" identity show | grep -E '^(nostr|arweave)')
  [ -n "$a" ] && [ "$a" = "$b" ]
}
check "identity export/import round-trips the same nostr and arweave ids" identity_case

status_case() {
  printf '2026-01-01T00:00:00Z\tnostr\tp\tnostr://x\tok\n' > "$T/led.tsv"; mkdir -p "$T/sd"; cp "$T/led.tsv" "$T/sd/published.tsv"
  GHLESS_DATA="$T/sd" "$ROOT/bin/ghless" status | grep -q 'nostr.*ok.*nostr://x' &&
  { "$ROOT/bin/ghless" publish "$FX" --dry-run 2>&1 | grep -q 'dry run'; } && "$ROOT/bin/ghless" completion bash | grep -q _ghless
}
check "status, publish --dry-run and completion work" status_case

hostile_import_case() {
  mkdir -p "$T/hx"; ln -s /etc/hostname "$T/hx/nostr.key"; (cd "$T/hx" && tar -czf "$T/hostile.tgz" nostr.key)
  GHLESS_DATA="$T/idH" "$ROOT/bin/ghless" identity import "$T/hostile.tgz" >/dev/null 2>&1 && return 1
  [ ! -e "$T/idH/nostr.key" ] || return 1
  (cd "$T" && GHLESS_DATA="$T/idR" "$ROOT/bin/ghless" identity init >/dev/null 2>&1; GHLESS_DATA="$T/idR" "$ROOT/bin/ghless" identity export rel.tgz >/dev/null 2>&1) || return 1
  [ -f "$T/rel.tgz" ] && [ ! -e "$T/idR/rel.tgz" ]
}
check "identity import refuses links; relative export lands in the cwd" hostile_import_case

relocate_case() {
  H6="$T/home6"; mkdir -p "$H6"; mkdir "$T/ra"; (cd "$ROOT" && git ls-files -z | tar --null -T - -cf -) | (cd "$T/ra" && tar -xf -)
  e() { env -u GHLESS_DATA -u GHLESS_WORK HOME="$H6" GHLESS_PREFIX="$H6/p" "$@"; }
  (cd "$T/ra" && e ./install --no-tools --no-skill) >/dev/null 2>&1 || return 1
  mv "$T/ra" "$T/rb"
  [ ! -e "$H6/.local/bin/ghless" ] || return 1
  (cd "$T/rb" && e ./install --no-tools --no-skill) >/dev/null 2>&1 || return 1
  [ "$(e "$H6/.local/bin/ghless" version)" = "$(cat "$ROOT/VERSION")" ] || return 1
  mv "$T/rb" "$T/rc"; rm -rf "$T/rc/libexec/__pycache__"
  (cd "$T/rc" && e ./install --uninstall) >/dev/null 2>&1
  [ -z "$(ls -A "$H6/.local/bin")" ]
}
check "install repairs links after the checkout is moved; uninstall removes them all" relocate_case

isolation_case() {
  H7="$T/home7"; mkdir -p "$T/xdgout/ghless/data" "$H7"; echo leak > "$T/xdgout/ghless/data/nostr.key"
  env -u GHLESS_PREFIX -u GHLESS_DATA -u GHLESS_WORK HOME="$H7" XDG_DATA_HOME="$T/xdgout" "$ROOT/install" --no-tools --no-skill >/dev/null 2>&1
  [ ! -e "$H7/.local/share/ghless/data/nostr.key" ] && [ ! -e "$T/xdgout/ghless/bin" ]
}
check "install never reads identities from outside HOME" isolation_case

uninstall_clean_case() {
  H8="$T/home8"; mkdir -p "$H8"
  env -u GHLESS_PREFIX -u GHLESS_DATA HOME="$H8" "$ROOT/install" --no-tools >/dev/null 2>&1
  env -u GHLESS_PREFIX -u GHLESS_DATA HOME="$H8" "$ROOT/install" --no-tools --uninstall >/dev/null 2>&1
  [ -z "$(find "$H8" -maxdepth 2 -not -path "$H8/.local*" -not -path "$H8" -print)" ]
}
check "uninstall leaves no empty profile files or skill dirs behind" uninstall_clean_case

migrate_case() {
  H5="$T/home5"; mkdir -p "$H5/.local/bin" "$H5/.local/share/errand/data" "$T/errand-home/bin"
  echo k > "$H5/.local/share/errand/data/nostr.key"; : > "$T/errand-home/bin/errand"
  ln -s "$T/errand-home/bin/errand" "$H5/.local/bin/errand"
  printf 'a=1\n# >>> errand-home >>>\nold\n# <<< errand-home <<<\nb=2\n' > "$H5/.profile"
  env -u GHLESS_PREFIX -u GHLESS_DATA -u GHLESS_WORK HOME="$H5" XDG_DATA_HOME= "$ROOT/install" --no-tools --no-skill >/dev/null 2>&1
  [ ! -e "$H5/.local/bin/errand" ] && ! grep -q 'errand-home' "$H5/.profile" && grep -q '^b=2$' "$H5/.profile" && [ -f "$H5/.local/share/ghless/data/nostr.key" ]
}
check "install migrates old errand-home links, profile block and identities" migrate_case

dry_case() {
  "$ROOT/bin/ghless" publish "$FX" --dry-run --lanes bogus >/dev/null 2>&1 && return 1
  "$ROOT/bin/ghless" publish "$T/nonexistent" --dry-run >"$T/dr.out" 2>&1 && return 1
  grep -q 'no such directory' "$T/dr.out"
}
check "dry-run fails like the real run: no ready lane, missing directory" dry_case

copies_case() {
  printf '%s\n' \
    "t	radicle	fx	rad:zABC123 (copy on: iris.radicle.xyz)	ok" \
    "t	nostr	fx	nostr://npubXYZ/relay.example/fx	ok" \
    "t	arweave	fx	parts: arweave://ADDR/fx-part1 arweave://ADDR/fx-part2	ok" \
    "t	swh	fx	https://example.org/fx	ok:200" \
    "t	wayback	fx	https://example.org/fx	ok(existing-or-new):https://web.archive.org/web/2026/https://example.org/fx" > "$T/led2.tsv"
  echo zipdata > "$T/fx-archive.zip"
  python3 "$ROOT/libexec/gitweb-static.py" --ledger "$T/led2.tsv" --download "fx=$T/fx-archive.zip" "$T/pub3" fx="$FX" || return 1
  pg="$T/pub3/fx/index.html"
  for want in 'api/v1/repos/rad:zABC123' 'gitworkshop.dev/npubXYZ/relay.example/fx' 'git clone arweave://ADDR/fx-part2' 'browse/origin/?origin_url=' 'web.archive.org/web/2026' 'href="downloads/fx-archive.zip"'; do
    grep -qF "$want" "$pg" || { echo "missing $want"; return 1; }
  done
  [ -f "$T/pub3/fx/downloads/fx-archive.zip" ] && grep -qF 'other copies' "$T/pub3/index.html"
}
check "tunnel page links every other copy and the archive download" copies_case

inject_case() {
  printf '#!/bin/sh\ntouch "%s/PWNED"\n' "$T" > "$T/evil.sh"; chmod +x "$T/evil.sh"
  "$ROOT/bin/ghless" fetch "file://$FX" "$T/i1" --ref "main --upload-pack=$T/evil.sh" >/dev/null 2>&1 && return 1
  "$ROOT/bin/ghless" fetch "file://$FX" "$T/i2" --depth "1 --upload-pack=$T/evil.sh" >/dev/null 2>&1 && return 1
  "$ROOT/bin/ghless" fetch "file://$FX --upload-pack=$T/evil.sh" "$T/i3" >/dev/null 2>&1 && return 1
  [ ! -e "$T/PWNED" ]
}
check "fetch refuses option injection through ref, depth and spec" inject_case

name_case() {
  for bad in .. . ...; do
    "$ROOT/bin/ghless" publish "$FX" --name "$bad" --lanes none >"$T/nm.out" 2>&1
    grep -q 'no usable name' "$T/nm.out" || { echo "name '$bad' was not refused for its name: $(cat "$T/nm.out")"; return 1; }
  done
  mkdir -p "$GHLESS_DATA" && : > "$GHLESS_DATA/nostr.key"
  python3 "$ROOT/libexec/gitweb-static.py" "$GHLESS_DATA/pub" "..=$FX" >/dev/null 2>&1 && return 1
  [ -e "$GHLESS_DATA/nostr.key" ] && [ ! -d "$GHLESS_DATA/.git" ]
}
check "names like .. are refused by publish and gitweb, data dir untouched" name_case

collide_case() {
  "$ROOT/bin/ghless" publish "$FX" --name same --lanes none >/dev/null 2>&1
  FX2="$T/fx2"; mkdir "$FX2"; git -C "$FX2" init -q -b main; : > "$FX2/f"; git -C "$FX2" add f; git -C "$FX2" -c user.name=t -c user.email=t@t commit -qm other
  "$ROOT/bin/ghless" publish "$FX2" --name same --lanes none >"$T/col.out" 2>&1 && return 1
  grep -q 'already publishes' "$T/col.out"
}
check "two projects cannot share a publish name" collide_case

pack_git_case() {
  git clone -q "$ROOT" "$T/packgit" 2>/dev/null || return 1
  [ -n "$(git -C "$ROOT" status --porcelain)" ] && { cp -a "$ROOT/." "$T/packgit/"; git -C "$T/packgit" add -A; git -C "$T/packgit" -c user.name=t -c user.email=t@t commit -qm wip; }
  mkdir -p "$T/packgit/state"; echo SECRETKEY > "$T/packgit/state/nostr.key"
  GHLESS_HOME="$T/packgit" "$T/packgit/bin/ghless" pack "$T/pg.zip" >/dev/null 2>&1 || return 1
  ! unzip -l "$T/pg.zip" | grep -q 'nostr\.key' && unzip -l "$T/pg.zip" | grep -q 'ghless/install'
}
gitcheck "pack of a dirty checkout never contains key files" pack_git_case

noshallow_case() {
  SH="$T/shallow"; git clone -q --depth 1 "file://$FX" "$SH" 2>/dev/null
  echo wip > "$SH/untracked.txt"
  (cd "$T" && "$ROOT/bin/ghless" archive "$SH" --no-net --out relout >"$T/ns.out" 2>&1) || return 1
  [ "$(git -C "$SH" rev-parse --is-shallow-repository)" = true ] || return 1
  z=$(ls "$T"/relout/*/untracked.tar.gz 2>/dev/null | head -n1); [ -n "$z" ]
}
check "archive --no-net stays offline, takes relative --out, keeps untracked files" noshallow_case

spn_case() {
  mkdir -p "$T/stub"
  printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s/args.log"\nprintf "{\\"job_id\\":\\"x\\"}\\n200"\n' "$T" > "$T/stub/curl"; chmod +x "$T/stub/curl"
  PATH="$T/stub:$PATH" sh "$ROOT/components/bulk-archive/bin/spn2.sh" -a SECRETKEY:abc -r 1 https://example.org >/dev/null 2>&1
  [ -s "$T/args.log" ] && ! grep -q SECRETKEY "$T/args.log"
}
check "spn2 keeps the access key off curl's argv" spn_case

if [ "$NET" = 1 ]; then
  check "read README (live)" sh -c "'$ROOT/bin/ghless' read octocat/Hello-World | head -c 20 | grep -q ."
  check "fetch live" sh -c "'$ROOT/bin/ghless' fetch octocat/Hello-World '$T/hw' --depth 1 >/dev/null 2>&1 && git -C '$T/hw' rev-parse ghless/source-base"

  pin_refusal() {
    cp -a "$ROOT" "$T/badroot" && sed -i 's/^NAK_SHA_x86_64=.*/NAK_SHA_x86_64="0000000000000000000000000000000000000000000000000000000000000000"/; s/^NAK_SHA_aarch64=.*/NAK_SHA_aarch64="0000000000000000000000000000000000000000000000000000000000000000"/' "$T/badroot/pins/pins.env"
    GHLESS_HOME="$T/badroot" GHLESS_PREFIX="$T/badprefix" "$T/badroot/libexec/tools" install nak >"$T/bad.out" 2>&1
    rc=$?
    [ $rc -ne 0 ] && [ ! -e "$T/badprefix/bin/nak" ] && grep -q 'sha256 mismatch' "$T/bad.out"
  }
  check "tampered pin is refused and leaves no binary" pin_refusal

  tamper_case() {
    GHLESS_HOME="$ROOT" GHLESS_PREFIX="$T/tp" "$ROOT/libexec/tools" install nak >/dev/null 2>&1 || return 1
    printf '#!/bin/sh\necho fake\n' > "$T/tp/bin/nak"
    GHLESS_HOME="$ROOT" GHLESS_PREFIX="$T/tp" "$ROOT/libexec/tools" install nak >/dev/null 2>&1 || return 1
    [ "$(sha256sum "$T/tp/bin/nak" | cut -d' ' -f1)" = "$(grep '^NAK_SHA_x86_64' "$ROOT/pins/pins.env" | cut -d'"' -f2)" ] || [ "$(uname -m)" != x86_64 ]
  }
  check "a replaced binary is reinstalled, not trusted" tamper_case

  install_case() {
    H="$T/home"; mkdir -p "$H"
    HOME="$H" GHLESS_PREFIX="$H/p" GHLESS_DATA= "$ROOT/install" --core --no-skill >"$T/i1.out" 2>&1 || return 1
    HOME="$H" GHLESS_PREFIX="$H/p" GHLESS_DATA= "$ROOT/install" --core --no-skill >"$T/i2.out" 2>&1 || return 1
    grep -q 'already installed' "$T/i2.out" || return 1
    [ "$(grep -c '>>> ghless' "$H/.profile")" = 1 ] || return 1
    [ -L "$H/.local/bin/ghless" ] && [ -x "$H/p/bin/cfrs" ] || return 1
    # profile without trailing newline, existing real gh, unterminated marker
    H2="$T/home2"; mkdir -p "$H2/.local/bin"
    printf '# mine' > "$H2/.profile"; printf 'echo realgh\n' > "$H2/.local/bin/gh"
    printf 'a=1\n# >>> ghless >>>\nb=2\n' > "$H2/.bashrc"
    HOME="$H2" GHLESS_PREFIX="$H2/p" GHLESS_DATA= "$ROOT/install" --core --no-skill >/dev/null 2>&1
    HOME="$H2" GHLESS_PREFIX="$H2/p" GHLESS_DATA= "$ROOT/install" --core --no-skill >/dev/null 2>&1
    sh -n "$H2/.profile" || return 1
    [ "$(grep -c '>>> ghless' "$H2/.profile")" = 1 ] || return 1
    grep -q '^# mine$' "$H2/.profile" || return 1
    [ "$(cat "$H2/.local/bin/gh")" = "echo realgh" ] || return 1
    [ "$(wc -l < "$H2/.bashrc")" = 3 ]
  }
  check "install --core twice in a scratch HOME is idempotent" install_case
fi

printf '\n%s passed, %s failed, %s skipped\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ]
