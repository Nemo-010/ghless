#!/usr/bin/env python3
"""Oracle comparison for ghforge/jqish.py against real gh's gojq.

For each expression: run `gh api repos/pkgforge/soar --jq EXPR`, and run the
same expression through jqish on the same payload. Compare stdout bytes; when
gh fails, its stderr line must match our JqError text and both must exit 1.
"""

import json
import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ghforge import jqish  # noqa: E402

REPO = "repos/pkgforge/soar"

EXPRESSIONS = [
    ".", ".name", ".missing", ".full_name", ".private", ".fork",
    ".stargazers_count", ".open_issues_count", ".owner", ".owner.login",
    ".license.spdx_id", '.["name"]', '.["full_name"]', ".topics",
    "1,2", "null", "true", "false", '"x"', "empty", ".name, .fork",
    ".name|length", ".name|ascii_upcase", ".topics|length",
    "1+1", "2/4", "10/4", "4/2", "10%3", "1e3", "3.0", "1.5", "1.999",
    ".stargazers_count/2", ".stargazers_count-900", "-0.5", "1000000*1000000",
    "null+1", '{"a":1}+{"b":2}', "[1]+[2]", '"x"*3', "1-1", ".fork|not",
    "{a:.name,b:.fork}", '{"z":1,"a":2}', "[.topics[]]", "[1.5]",
    '".\\(.name)-\\(.fork)"', "{}", "[]",
    ".topics[]", '[.topics[]|select(startswith("a"))]',
    '[.topics[]|select(length>8)]|length', ".topics|map(select(length>8))",
    ".topics|map(ascii_upcase)|length", ".owner|keys|length", ".owner|keys",
    ".topics|sort|.[0]", ".topics|.[0:2]", ".topics[1:3]", ".name[0:2]",
    '.topics|index("rust")', '.topics|contains(["rust"])',
    '.name|split("a")', '.name|ltrimstr("so")', '.name|startswith("so")',
    '.name|endswith("ar")', '.description|test("soar")',
    '.name|test("SOAR"; "i")', '.topics|join(",")',
    ".topics|@csv", ".topics|@tsv", ".topics|@json", ".name|@uri", ".topics|@base64",
    ".name|tojson|fromjson", ".topics|length|tostring", ".stargazers_count|tostring",
    'if .fork then "y" else "n" end', 'try .nope[] catch "err"',
    '.name//"none"', '.missing//"d"', "empty//3", "(.topics|length) as $n | $n*2",
    "true and true", "true and false", "false and true", "false or false",
    "1 and 1", 'true or error("x")', 'false and error("x")',
    "[range(0;3)]", "[limit(2; range(10))]", ".topics|first", ".topics|last",
    ".topics|nth(2)", ".topics|unique|length", ".topics|group_by(length)|length",
    ".topics|sort_by(.)|.[0]", ".topics|min", ".topics|max", "[1,2,3]|add",
    ".topics|to_entries|length", ".owner|to_entries|.[0].key",
    '{"a":1}|to_entries|from_entries|.a', ".name|explode|length",
    '"soar"|implode', '.owner|has("login")', '.owner|has("nope")',
    ".topics|flatten|length", "[[1],[2,[3]]]|flatten", "[3,1,2]|sort",
    ".a[", ".topics|implode", ".name|.foo", ".topics|.foo", "5|.foo",
    "5|.[]", '1+"x"', '1-"x"', '1*"x"', '1%"x"', '1/"x"',
    ".fork|length", ".name|keys", ".stargazers_count|keys", "5|sort", "5|add",
    "5|unique", "5|map(.)", "5|to_entries", "5|flatten", "5|reverse", "5|min",
    "5|first", "5|last", "5|floor", ".name|floor", ".name|tonumber",
    ".topics|join(5)", ".name|split(5)", ".topics|contains(1)",
    ".name|startswith(1)", ".name|endswith(5)", ".name|ltrimstr(5)",
    '5|has("a")', '5|test("x")', ".name|@csv", "5|@csv", ".owner|@tsv",
    ".name|explode", ".stargazers_count[1:2]", ".name[.topics]",
    'false or error("x")', "error({a:1})", "error(null)",
    ".name|ascii_downcase|tonumber", "foo", ".topics|length>3 and .fork",
]


def real_run(expr):
    proc = subprocess.run(["gh", "api", REPO, "--jq", expr],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def mine_run(expr, data):
    try:
        text = jqish.run(expr, data)
    except jqish.JqError as exc:
        return 1, "", exc.text
    return 0, text, ""


def main():
    proc = subprocess.run(["gh", "api", REPO], capture_output=True, text=True)
    if proc.returncode != 0:
        print("oracle unavailable: %s" % proc.stderr.strip()[:120])
        return 2
    data = json.loads(proc.stdout)

    total = same = 0
    failures = []
    for expr in EXPRESSIONS:
        total += 1
        rc, out, err = real_run(expr)
        mrc, mout, merr = mine_run(expr, data)
        if rc == mrc == 0:
            if out == mout:
                same += 1
                continue
            failures.append((expr, "stdout real=%r mine=%r" % (out[:110], mout[:110])))
            continue
        if rc != 0 and mrc != 0:
            if err.strip().splitlines() == merr.strip().splitlines():
                same += 1
                continue
            failures.append((expr, "stderr real=%r mine=%r" % (err.strip()[:110], merr.strip()[:110])))
            continue
        failures.append((expr, "exit real=%s mine=%s (real err=%r mine err=%r)"
                         % (rc, mrc, err.strip()[:80], merr.strip()[:80])))

    print("%d/%d identical" % (same, total))
    for expr, why in failures:
        print("\n  DIFF %s\n    %s" % (expr, why))
    return 0 if same == total else 1


if __name__ == "__main__":
    sys.exit(main())
