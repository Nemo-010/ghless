#!/usr/bin/env python3
"""Oracle comparison for ghforge/cmds/search.py against real `gh search`."""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WRAPPER = os.path.join(ROOT, "bin", "gh")

CASES = [
    "search repos --owner pkgforge --limit 3",
    "search repos --owner pkgforge --limit 2 --json fullName,stargazersCount,description",
    "search repos --owner pkgforge --limit 1 --json owner,license,language,visibility",
    "search repos 'topic:appimage' --limit 2 --json fullName",
    "search repos --owner pkgforge --language rust --limit 2",
    "search repos --owner pkgforge --stars >100 --limit 2 --json fullName,stargazersCount",
    "search repos --owner pkgforge --sort stars --limit 2 --json fullName,stargazersCount",
    "search issues --owner pkgforge --limit 3",
    "search issues --owner pkgforge --limit 2 --json number,title,state,repository,author",
    "search issues --owner pkgforge --limit 2 --json assignees,labels,commentsCount,isLocked",
    "search issues --owner pkgforge --state closed --limit 2 --json number,state",
    "search issues --owner pkgforge --label enhancement --limit 2 --json number,labels",
    "search prs --owner pkgforge --limit 2",
    "search prs --owner pkgforge --limit 1 --json number,title,isDraft,state",
    "search code --owner pkgforge soar --limit 2",
    "search code --owner pkgforge soar --limit 1 --json path,repository",
    "search code --owner pkgforge soar --limit 1 --json textMatches",
    "search code --owner pkgforge --filename CHANGELOG.md soar --limit 1 --json path",
    "search commits --owner pkgforge fix --limit 2",
    "search commits --owner pkgforge fix --limit 1 --json sha,commit",
    "search commits --owner pkgforge fix --limit 1 --json author,committer,repository,url",
    "search repos --owner pkgforge --limit 2 --jq '.[].fullName'",
    "search issues --owner pkgforge --limit 2 --json number --jq 'map(.number)'",
    "search repos 'topic:zzzznopezzzz' --limit 2 --json fullName",
]


def run(cmd, mode):
    env = dict(os.environ)
    env["GH_FORGE_MODE"] = mode
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def main():
    total = same = 0
    failures = []
    for case in CASES:
        total += 1
        real = run(["gh"] + case.split(" ", 1) if False else ["gh"] + _split(case),
                   "real")
        mine = run([WRAPPER] + _split(case), "forge")
        if real.returncode == mine.returncode and \
                real.stdout == mine.stdout and \
                real.stderr.strip() == mine.stderr.strip():
            same += 1
            continue
        failures.append((case, real, mine))

    print("%d/%d identical" % (same, total))
    for case, real, mine in failures:
        print("\n### DIFF: gh %s" % case)
        if real.returncode != mine.returncode:
            print("  exit real=%s mine=%s" % (real.returncode, mine.returncode))
        if real.stdout != mine.stdout:
            print("  stdout real=%r" % real.stdout[:200])
            print("  stdout mine=%r" % mine.stdout[:200])
        if real.stderr.strip() != mine.stderr.strip():
            print("  stderr real=%r" % real.stderr.strip()[:200])
            print("  stderr mine=%r" % mine.stderr.strip()[:200])
    return 0 if same == total else 1


def _split(case):
    import shlex
    return shlex.split(case)


if __name__ == "__main__":
    sys.exit(main())
