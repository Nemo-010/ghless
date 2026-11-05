# parse_spec SPEC: sets S_URL (clone URL), S_OWNER, S_REPO, S_REF, S_PATH, S_GITHUB (1/0).
# shellcheck shell=sh
parse_spec() {
  S_REF=""; S_PATH=""; S_OWNER=""; S_REPO=""; S_GITHUB=0; S_EXTRA=""
  _s="$1"
  case "$_s" in
    ""|-*|*[[:space:]]*|*[[:cntrl:]]*) return 1 ;;
  esac
  case "$_s" in
    git@github.com:*) _s="https://github.com/${_s#git@github.com:}" ;;
    github.com/*) _s="https://$_s" ;;
    http://*|https://*|ssh://*|git://*|file://*) ;;
    /*|./*|../*) S_URL="$_s"; return 0 ;;
    */*) _s="https://github.com/$_s" ;;
    *) return 1 ;;
  esac
  case "$_s" in
    https://github.com/*|http://github.com/*)
      S_GITHUB=1
      _p="${_s#*github.com/}"; _p="${_p%%\?*}"; _p="${_p%%#*}"
      case "$_p" in */*) ;; *) return 1 ;; esac
      S_OWNER="${_p%%/*}"; _r="${_p#*/}"
      S_REPO="${_r%%/*}"; S_REPO="${S_REPO%.git}"
      _rest=""; case "$_r" in */*) _rest="${_r#*/}" ;; esac
      case "$_rest" in
        tree/*|blob/*|raw/*)
          _rest="${_rest#*/}"; S_REF="${_rest%%/*}"
          case "$_rest" in */*) S_PATH="${_rest#*/}" ;; esac ;;
        "") ;;
        *) S_EXTRA="$_rest" ;;
      esac
      [ -n "$S_OWNER" ] && [ -n "$S_REPO" ] || return 1
      S_URL="https://github.com/$S_OWNER/$S_REPO.git" ;;
    *) S_URL="$_s" ;;
  esac
}
