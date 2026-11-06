"""Read-only access to a bare git repository through the git command."""
import subprocess

MAX_VIEW = 512 * 1024
MAX_RAW = 4 * 1024 * 1024
MAX_DIFF = 256 * 1024


def git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True).stdout


class BlobReader:
    """One long-lived `git cat-file --batch` so a repo with thousands of files costs one process."""

    def __init__(self, repo):
        self.proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=repo,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, spec):
        self.proc.stdin.write(spec.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if len(header) < 3:
            return None
        size = int(header[2])
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)
        return data

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def is_text(data):
    return b"\0" not in data[:8000]


def tree_entries(repo):
    """[(path, size)] of every blob at HEAD, submodules excluded, unsafe paths dropped."""
    raw = git("ls-tree", "-r", "-l", "-z", "HEAD", cwd=repo).decode("utf-8", "replace").split("\0")
    out = []
    for entry in raw:
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, _sha, size = meta.split()
        if kind != "blob" or mode == "160000":
            continue
        if path.startswith("/") or ".." in path.split("/"):
            continue
        out.append((path, int(size) if size.isdigit() else 0))
    return out


def commits(repo, limit):
    fmt = "%H%x09%h%x09%an%x09%ad%x09%s"
    text = git("log", "-n", str(limit), f"--format={fmt}", "--date=short", cwd=repo).decode("utf-8", "replace")
    rows = []
    for line in text.splitlines():
        parts = (line.split("\t", 4) + [""] * 5)[:5]
        rows.append(dict(zip(("sha", "short", "author", "date", "subject"), parts)))
    return rows


def commit_count(repo):
    return int(git("rev-list", "--count", "HEAD", cwd=repo).decode().strip() or 0)


def refs(repo, kind):
    text = git("for-each-ref", "--sort=-creatordate", "--format=%(refname:short)%09%(objectname:short)%09%(creatordate:short)",
               f"refs/{kind}", cwd=repo).decode("utf-8", "replace")
    return [tuple((line.split("\t") + ["", ""])[:3]) for line in text.splitlines() if line]


def show(repo, sha):
    """(message body, diff text) of one commit; the diff is cut at MAX_DIFF."""
    body = git("show", "-s", "--format=%B", sha, cwd=repo).decode("utf-8", "replace").strip()
    diff = git("show", "--format=", "--stat", "--patch", "-M", sha, cwd=repo)
    truncated = len(diff) > MAX_DIFF
    return body, diff[:MAX_DIFF].decode("utf-8", "replace"), truncated
