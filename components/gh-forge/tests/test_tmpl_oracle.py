#!/usr/bin/env python3
"""Oracle comparison for ghforge/gotmpl.py against real gh's --template."""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ghforge import gotmpl  # noqa: E402

REPO = "repos/pkgforge/soar"

TEMPLATES = [
    "{{.name}}", "{{.owner.login}}", "{{.nope}}", "{{.nope.deep}}",
    "{{.name}} {{.stargazers_count}}", "{{.license}}", "{{.topics}}",
    "{{printf \"%v\" .name}}|{{printf \"%v\" .fork}}|{{printf \"%v\" .mirror_url}}",
    "{{printf \"%v\" .license}}",
    "{{printf \"%d\" .stargazers_count}}",
    "{{printf \"%s\" .name}}", '{{printf "%s" "a\\"b"}}',
    "{{printf \"%.2f\" 1.5}}", "{{printf \"%v\" 2.0}}",
    "{{printf \"%s %s\" .name}}",
    "{{if .fork}}y{{else}}n{{end}}",
    "{{if .fork}}y{{else if .private}}p{{else}}n{{end}}",
    "{{if not .fork}}A{{else}}B{{end}}",
    "{{if and .name .fork}}A{{else}}B{{end}}",
    "{{if or .fork .name}}A{{else}}B{{end}}",
    "{{if eq .name \"soar\"}}yes{{end}}",
    "{{if ne .name \"x\"}}yes{{end}}",
    "{{if gt .stargazers_count 100.0}}big{{end}}",
    "{{if le .stargazers_count 100000}}ok{{end}}",
    "{{with .owner}}{{.login}}{{end}}",
    "{{$n := .name}}{{$n}}!",
    "{{range .topics}}{{.}},{{end}}",
    "{{range .nope}}x{{else}}empty{{end}}",
    "{{range $i, $t := .topics}}{{$i}}:{{$t}} {{end}}",
    "{{range .topics}}{{if eq . \"linux\"}}{{break}}{{end}}{{.}}{{end}}",
    "{{range .topics}}{{if eq . \"linux\"}}{{continue}}{{end}}{{.}} {{end}}",
    "{{range .owner}}{{$k}}={{end}}",
    "{{len .topics}}{{len .name}}",
    "{{index .topics 0}}{{index .topics 3}}",
    "{{$i := 1}}{{index .topics $i}}",
    "a{{/* c */}}b",
    "a {{- .name -}} b",
    "{{printf \"%s!\" .name}}",
    "{{.name | printf \"%s!\"}}",
    "{{join .topics \",\"}}",
    "{{.topics | join \",\"}}",
    "{{.name | printf \"%s\" | printf \"%s!\"}}",
    "{{truncate 21 .description}}",
    "{{truncate 3 .name}}",
    "{{truncate 2 .name}}",
    "{{truncate 100 .name}}",
    "{{timeago .pushed_at}}",
    "{{timeago .created_at}}",
    "{{tablerow \"aa\" \"b\"}}{{tablerender}}",
    "{{tablerow \"aaa\" \"b\"}}{{tablerender}}{{tablerow \"c\" \"d\"}}{{tablerender}}",
    "{{tablerow \"\" \"x\"}}{{tablerender}}",
    "{{tablerow \"a\"}}{{tablerender}}",
    "{{tablerow \"a\" \"b\"}}",
    "{{tablerender}}",
    "{{tablerow \"a\" \"b\"}}{{tablerender}}{{tablerender}}",
    "{{range .topics}}{{tablerow . .}}{{tablerender}}{{end}}",
    "{{hyperlink \"http://x\" \"y\"}}",
    "{{color \"red\" \"hi\"}}",
    "{{autocolor \"red\" \"hi\"}}",
    "{{autocolor \"\" \"hi\"}}",
    "{{color \"bogus\" \"x\"}}",
    "{{hyperlink \"http://x\" \"y\"}}",
    "{{pluck \"name\" .labels}}",
    "{{pluck \"name\" .labels | join \",\"}}",
    "{{pluck \"nope\" .labels}}",
    "{{.license.spdx_id}}",
    "{{range $k, $v := .languages}}{{$k}} {{end}}",
    # error paths
    "{{nope}}", "{{.name", "a{{end}}", "{{len .mirror_url}}",
    "{{index .topics 99}}", "{{timeago \"garbage\"}}",
    "{{printf \"%d\" .name}}", "{{truncate .description 21}}",
]


def main():
    repo = json.loads(subprocess.run(["gh", "api", REPO],
                                     capture_output=True, text=True).stdout)

    total = same = 0
    failures = []
    for tpl in TEMPLATES:
        total += 1
        real = subprocess.run(["gh", "api", REPO, "-t", tpl],
                              capture_output=True, text=True)
        try:
            mine = gotmpl.render(tpl, repo)
            mrc, merr = 0, ""
        except gotmpl.TemplateError as exc:
            mine, mrc, merr = None, 1, str(exc)
        if real.returncode == 0 and mrc == 0:
            if real.stdout == mine:
                same += 1
                continue
            failures.append((tpl, "out real=%r mine=%r" % (real.stdout[:100], (mine or "")[:100])))
            continue
        if real.returncode != 0 and mrc != 0:
            rline = real.stderr.strip().splitlines()[0] if real.stderr.strip() else ""
            if rline == merr.strip() or rline.replace("template: :1:", "") == merr.strip():
                same += 1
                continue
            failures.append((tpl, "err real=%r mine=%r" % (rline[:100], merr[:100])))
            continue
        failures.append((tpl, "rc real=%s mine=%s real_err=%r mine=%r"
                         % (real.returncode, mrc, real.stderr.strip()[:80], merr[:80])))

    print("%d/%d identical" % (same, total))
    for tpl, why in failures:
        print("\n  DIFF %s\n    %s" % (tpl, why))
    return 0 if same == total else 1


if __name__ == "__main__":
    sys.exit(main())
