#!/bin/sh
# gh-forge: put the wrapper on PATH ahead of the real gh, or undo that.
# Usage: install.sh [uninstall]
set -e

BIN_DIR="${GH_FORGE_BIN:-$HOME/.local/bin}"
SRC="$(cd "$(dirname "$0")" && pwd)"

case "${1:-install}" in
install)
    mkdir -p "$BIN_DIR"
    ln -sfn "$SRC/bin/gh" "$BIN_DIR/gh"
    echo "gh-forge installed: $BIN_DIR/gh -> $SRC/bin/gh"
    echo "make sure $BIN_DIR is ahead of the real gh in PATH"
    ;;
uninstall)
    rm -f "$BIN_DIR/gh"
    echo "gh-forge removed from $BIN_DIR"
    ;;
*)
    echo "usage: install.sh [install|uninstall]" >&2
    exit 2
    ;;
esac
