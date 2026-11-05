#!/bin/sh
# bench: wall-clock timings of the commands a new agent runs first.
#   sh tests/bench.sh [RUNS]      default 5 runs per command; prints min / median / max in ms
# Conditions are printed first. Network commands depend on the network at the time.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNS="${1:-5}"
T=$(mktemp -d "${TMPDIR:-/tmp}/ghless-bench.XXXXXX")
trap 'rm -rf "$T"' EXIT
export GHLESS_HOME="$ROOT" GHLESS_PREFIX="$T/prefix" GHLESS_DATA="$T/data"
G="$ROOT/bin/ghless"

now_ms() { date +%s%N | cut -c1-13; }
bench() { # bench LABEL CMD...   (CMD gets run via sh -c; {n} is replaced by the run number)
  label="$1"; shift
  : > "$T/times"
  i=1
  while [ "$i" -le "$RUNS" ]; do
    cmd=$(printf '%s' "$*" | sed "s/{n}/$i/g")
    s=$(now_ms); sh -c "$cmd" >/dev/null 2>&1; rc=$?; e=$(now_ms)
    echo "$((e - s))" >> "$T/times"
    [ "$rc" -eq 0 ] || echo "  (run $i exited $rc)" >&2
    i=$((i + 1))
  done
  sort -n "$T/times" | awk -v l="$label" '{a[NR]=$1} END {printf "%-44s min %6d  median %6d  max %6d ms\n", l, a[1], a[int((NR+1)/2)], a[NR]}'
}

echo "host: $(uname -srm); date: $(date -u +%Y-%m-%dT%H:%M:%SZ); runs per command: $RUNS"
echo "proxy env: $( [ -n "${HTTPS_PROXY:-}${https_proxy:-}" ] && echo yes || echo no ); cores: $(nproc)"
bench "ghless version" "$G version"
bench "ghless help" "$G help"
bench "ghless doctor --brief" "$G doctor --brief"
bench "ghless doctor (network probes)" "$G doctor"
bench "ghless read octocat/Hello-World (README)" "$G read octocat/Hello-World"
bench "ghless read ... README.md (raw route)" "$G read octocat/Hello-World README"
bench "ghless fetch --depth 1 (fresh dir)" "$G fetch octocat/Hello-World $T/f{n} --depth 1"
bench "ghless fetch full history (fresh dir)" "$G fetch octocat/Hello-World $T/ff{n}"
bench "gh repo view (forge mode, cold cache)" "XDG_CACHE_HOME=$T/c{n} GH_FORGE_MODE=forge $ROOT/components/gh-forge/bin/gh repo view octocat/Hello-World --json name"
bench "gh repo view (auto mode, cold cache)" "XDG_CACHE_HOME=$T/ca{n} $ROOT/components/gh-forge/bin/gh repo view octocat/Hello-World --json name"
bench "gh repo view (auto mode, warm cache)" "XDG_CACHE_HOME=$T/ca1 $ROOT/components/gh-forge/bin/gh repo view octocat/Hello-World --json name"
bench "curl api.github.com repo (reference)" "curl -sf https://api.github.com/repos/octocat/Hello-World"
