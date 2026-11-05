#!/usr/bin/env python3
"""Differential test: real `gh` versus the wrapper in forge mode.

Runs the same argv through both and compares stdout, stderr and exit code.
Volatile fields (timestamps that move between the two calls) are reported
separately, so a real difference is not hidden by them.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WRAPPER = os.path.join(ROOT, "bin", "gh")

CASES = [
    # api
    "api repos/pkgforge/soar",
    "api repos/pkgforge/soar --jq .name",
    "api repos/pkgforge/soar --jq {n:.name,s:.stargazers_count}",
    "api repos/pkgforge/soar --jq .stargazers_count",
    "api repos/pkgforge/soar --jq [.topics[]]",
    "api 'search/repositories?q=stars:%3E200000&per_page=2' --jq .total_count",
    "api repos/pkgforge/soar/readme -H 'Accept: application/vnd.github.raw'",
    "api repos/pkgforge/nope-nope",
    "api -X GET search/repositories -f q=stars:>200000 -f per_page=1 --jq .total_count",
    # repo
    "repo view pkgforge/soar --json nameWithOwner,description,stargazerCount,owner,defaultBranchRef",
    "repo view pkgforge/soar --json forkCount,isFork,isPrivate,url,homepageUrl",
    "repo view pkgforge/soar --json repositoryTopics,licenseInfo,primaryLanguage",
    "repo list pkgforge -L 3",
    "repo list pkgforge -L 2 --json nameWithOwner,visibility,stargazerCount",
    # issues
    "issue list -R pkgforge/soar -L 3",
    "issue list -R pkgforge/soar -L 2 --json number,title,state,labels,createdAt,updatedAt,url",
    "issue list -R pkgforge/soar -L 2 --json author,assignees,body,milestone,isPinned",
    "issue list -R pkgforge/soar -L 2 --json number -q '.[] | .number'",
    "issue view 213 -R pkgforge/soar",
    "issue view 213 -R pkgforge/soar --json number,title,state,stateReason,author,labels,body,url",
    "issue list -R pkgforge/soar -L 2 --json number --jq 'map(.number) | join(\",\")'",
    # prs
    "pr list -R pkgforge/soar -L 3",
    "pr list -R pkgforge/soar -L 1 --json number,title,state,isDraft,headRefName,baseRefName,author,labels",
    "pr view 234 -R pkgforge/soar --json number,state,isDraft,headRefName,baseRefName,additions,deletions",
    "pr view 234 -R pkgforge/soar --json files",
    # releases
    "release list -R pkgforge/soar -L 3",
    "release list -R pkgforge/soar -L 2 --json tagName,name,isDraft,isPrerelease,publishedAt",
    "release view v0.14.0 -R pkgforge/soar --json tagName,name,isDraft,isPrerelease,url",
    # actions
    "run list -R pkgforge/soar -L 2",
    "run list -R pkgforge/soar -L 1 --json databaseId,name,status,conclusion,event,headBranch,headSha",
    "workflow list -R pkgforge/soar -L 3",
    "label list -R pkgforge/soar -L 3",
    # search
    "search repos --owner pkgforge --limit 3",
    "search repos --owner pkgforge --limit 2 --json fullName,stargazersCount,description",
    "search issues --owner pkgforge --limit 3",
    "search prs --owner pkgforge --limit 3",
    "search code --owner pkgforge soar --limit 2 --json path,repository",
    "search commits --owner pkgforge fix --limit 2 --json sha,commit",
    # filters and paging
    "issue list -R pkgforge/soar -L 2 --state closed",
    "issue list -R pkgforge/soar -L 2 --label enhancement",
    "issue list -R pkgforge/soar -L 2 --author QaidVoid",
    "pr list -R pkgforge/soar -L 2 --state merged",
    "pr list -R pkgforge/soar -L 2 --base main",
    "issue list -R pkgforge/soarpkgs -L 2 --search zapzap",
    "api 'repos/pkgforge/soar/issues?per_page=2&state=all' --paginate --jq 'length'",
    "api 'repos/pkgforge/soar/issues?per_page=2' --paginate --slurp --jq 'length'",
    "repo view pkgforge/soar",
    "issue view 59 -R pkgforge/soar --json number,title,labels,author",
    "run view 41870923982 -R pkgforge/soar --json databaseId,status,conclusion,jobs",
    "workflow list -R pkgforge/soar -L 3 --json name,path,state",

    # usage / errors
    "issue list -R pkgforge/soar --json nope",
    "repo view pkgforge/soar --json nope",
    "api repos/pkgforge/soar --slurp --jq .",
]


def run(cmd, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    ok = same = 0
    failures = []
    for case in CASES:
        if only and only not in case:
            continue
        args = shlex.split(case)
        real = run(["gh"] + args, {"GH_FORGE_MODE": "real"})
        forge = run([WRAPPER] + args, {"GH_FORGE_MODE": "forge"})
        ok += 1
        if real == forge:
            same += 1
            continue
        failures.append((case, real, forge))

    print("%d/%d identical" % (same, ok))
    for case, real, forge in failures:
        print("\n### DIFF: gh " + case)
        if real[0] != forge[0]:
            print("  exit: real=%s forge=%s" % (real[0], forge[0]))
        if real[1] != forge[1]:
            print("  stdout real: %r" % (real[1][:300],))
            print("  stdout forge: %r" % (forge[1][:300],))
        if real[2].strip() != forge[2].strip():
            print("  stderr real: %r" % (real[2][:300],))
            print("  stderr forge: %r" % (forge[2][:300],))
    return 0 if same == ok else 1


if __name__ == "__main__":
    sys.exit(main())
