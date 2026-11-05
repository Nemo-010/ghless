#!/usr/bin/env python3
"""Split a repo's HEAD into part repos small enough for Arweave's free tier.

usage: arweave-split.py SRC_REPO OUTDIR NAME ADDRESS [LIMIT_BYTES]

Top-level entries of HEAD (directories that are too big are split into their children) are grouped greedily so that each group's tar.gz stays
under LIMIT_BYTES (default 75000; the free tier is below 100 KiB per upload).
Each group becomes OUTDIR/NAME-partK, a git repo on branch main (kept between runs;
a changed group adds a commit) holding the group's files plus PARTS.md, which lists every part, the source
commit and how to reassemble. Prints one part name per line. Exits 1 when a
single top-level entry cannot fit.
"""
import gzip
import io
import os
import shutil
import subprocess
import sys
import tarfile


def git(repo, *args, data=False):
    out = subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True).stdout
    return out if data else out.decode()


def gz_size(repo, paths):
    tar = git(repo, "archive", "--format=tar", "HEAD", "--", *paths, data=True)
    return len(gzip.compress(tar, 9))


def main():
    if len(sys.argv) not in (5, 6):
        print(__doc__, file=sys.stderr)
        return 2
    src, out, name, address = sys.argv[1:5]
    limit = int(sys.argv[5]) if len(sys.argv) == 6 else 75000
    def children(path):
        args = ["ls-tree", "--name-only", "-z", "HEAD"] + ([path + "/"] if path else [])
        return [e for e in git(src, *args).split("\0") if e]

    def is_tree(path):
        return git(src, "cat-file", "-t", f"HEAD:{path}").strip() == "tree"

    def expand(path):
        if gz_size(src, [path]) <= limit:
            return [path]
        if not is_tree(path):
            print(f"{path}: alone it exceeds {limit} bytes compressed", file=sys.stderr)
            raise SystemExit(1)
        out = []
        for child in children(path):
            out.extend(expand(child))
        return out

    entries = []
    for top in children(""):
        entries.extend(expand(top))
    groups, current = [], []
    for entry in entries:
        if current and gz_size(src, current + [entry]) > limit:
            groups.append(current)
            current = []
        current.append(entry)
    if current:
        groups.append(current)

    commit = git(src, "rev-parse", "HEAD").strip()
    names = [f"{name}-part{i + 1}" for i in range(len(groups))]
    listing = "\n".join(f"- arweave://{address}/{n}: {', '.join(g)}" for n, g in zip(names, groups))
    manifest = (f"# {name}: part @K@ of {len(groups)}\n\n"
                f"Source commit: {commit}\n\nParts:\n{listing}\n\n"
                "Reassemble: clone every part and copy the top-level entries of each into one directory.\n"
                "The full history lives on the other lanes (radicle, nostr) when they succeeded.\n")
    ident = ["-c", "user.name=errand", "-c", "user.email=errand@users.noreply.invalid"]
    for i, (n, group) in enumerate(zip(names, groups)):
        dest = os.path.join(out, n)
        if os.path.isdir(os.path.join(dest, ".git")):
            subprocess.run(["git", "-C", dest, "rm", "-rq", "--ignore-unmatch", "."], check=True)
            for leftover in os.listdir(dest):
                if leftover != ".git":
                    path = os.path.join(dest, leftover)
                    shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        else:
            os.makedirs(dest, exist_ok=True)
            subprocess.run(["git", "-C", dest, "init", "-q", "-b", "main"], check=True)
        tar = git(src, "archive", "--format=tar", "HEAD", "--", *group, data=True)
        with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
            try:
                tf.extractall(dest, filter="data")
            except TypeError:
                tf.extractall(dest)
        with open(os.path.join(dest, "PARTS.md"), "w") as fh:
            fh.write(manifest.replace("@K@", str(i + 1)))
        subprocess.run(["git", "-C", dest, "add", "-A"], check=True)
        if subprocess.run(["git", "-C", dest, "diff", "--cached", "--quiet"]).returncode != 0:
            subprocess.run(["git", "-C", dest, *ident, "commit", "-q", "-m",
                            f"{name} part {i + 1}/{len(groups)} of {commit[:12]}"], check=True)
        print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
