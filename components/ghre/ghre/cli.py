"""Command line for ghre.

Two subcommands, one per phase, plus a report that renders the ledger.

    ghre ledger <account> --out <dir>
    ghre recover <dir> [--account NAME] [--sha SHA ...]
    ghre report <dir>

The split is deliberate: a ledger run is cheap and always safe, and it is what
you hand to somebody else when you want them to know what existed. A recovery
run costs requests and may fail in many ways without changing the ledger's
accuracy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .broker import Budget, Client
from . import events as events_lane
from .enumerate import (
    enumerate_account,
    enumerate_contributions,
    enumerate_repo,
)
from .ledger import Ledger
from .recover import (
    mine_snippets,
    recover_archive,
    recover_object,
    recover_refs,
)
from .snapshot import (
    SaveQueue,
    merge_saves,
    plan_urls,
    recover_note,
    snapshot_repo,
    verify,
    zip_snapshot,
)

DEFAULT_TERMS = [
    "README",
    "REPORT.md",
    "AUDIT.md",
    "measurements",
    "roadmap",
    "design",
]


def _client(args: argparse.Namespace, out: Path) -> Client:
    budget = Budget(limit=args.budget, log_path=out / "requests.tsv")
    return Client(
        budget=budget,
        timeout=args.timeout,
        token=args.token,
        route=args.transport,
    )


def cmd_ledger(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = _client(args, out)

    seeds = [n for item in (args.seed or []) for n in item.split(',') if n.strip()]
    for path in args.seed_file or []:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                seeds.append(line)
    orgs = [o for item in (args.org or []) for o in item.split(',') if o.strip()]
    ledger = enumerate_account(
        client, args.account, seeds=seeds, workdir=out, orgs=orgs
    )
    print(f"account: {args.account}  repos found: {len(ledger.repos())}")

    owned = [r for r in ledger.repos() if r.split("/")[0].lower() == args.account.lower()]
    for repo in owned:
        owner, _, name = repo.partition("/")
        enumerate_repo(client, ledger, owner, name)
        print(f"  listed {repo}")

    print(f"repos known: {len(ledger.repos())} ({len(owned)} owned by the account)")

    ledger.save(out / "ledger.json")
    (out / "LEDGER.md").write_text(render_ledger(ledger), encoding="utf-8")
    print(f"wrote {out / 'ledger.json'} ({len(ledger.entries)} entries)")
    if ledger.gaps:
        print(f"gaps: {len(ledger.gaps)} (see LEDGER.md)")
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    workdir = Path(args.dir)
    ledger_path = workdir / "ledger.json"
    if not ledger_path.exists():
        print(f"no ledger at {ledger_path}; run `ghre ledger` first", file=sys.stderr)
        return 1

    ledger = Ledger.load(ledger_path)
    account = args.account or ledger.account
    client = _client(args, workdir)

    if not args.skip_refs:
        count = recover_refs(
            ledger,
            workdir,
            account,
            extra_repos=args.repo or [],
            git_base=client.git_url("", "").rstrip("/"),
        )
        print(f"refs: recovered {count} commit patches")
    if args.sha:
        count = recover_object(
            ledger,
            workdir,
            account,
            args.sha,
            extra_repos=args.repo or [],
            git_base=client.git_url("", "").rstrip("/"),
        )
        print(f"objects: recovered {count} by sha")
    if not args.skip_archive:
        count = recover_archive(client, ledger, workdir, account)
        print(f"archive: recovered {count} pages")
    if not args.skip_snippets:
        count = mine_snippets(
            client, ledger, workdir, account, terms=args.term or DEFAULT_TERMS
        )
        print(f"snippets: recovered {count} distinct windows")

    ledger.save(ledger_path)
    (workdir / "LEDGER.md").write_text(render_ledger(ledger), encoding="utf-8")
    print(f"updated {ledger_path}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Archive one repository's issues and pull requests, and optionally ask
    an independent archive for a copy of every page.

    This is the only lane that runs before a loss, which changes what it is
    allowed to assume: no enumeration tricks, no recovery of gaps, just the
    API's own answer for every object it will name.
    """
    owner, _, name = args.repo.partition("/")
    if not owner or not name:
        print("expected owner/repo", file=sys.stderr)
        return 2
    outdir = Path(args.out)
    client = _client(args, outdir if outdir.parent != Path("") else Path("."))

    if args.verify:
        problems = verify(outdir)
        if problems:
            for problem in problems:
                print(f"  {problem}")
            print(f"{len(problems)} problem(s) in {outdir}")
            return 1
        print(f"{outdir}: manifest matches every file")
        return 0

    data = snapshot_repo(
        client, outdir, owner, name, pause=args.pause, progress=print
    )
    issues = sum(1 for row in data["objects"] if row["kind"] == "issue")
    pulls = len(data["objects"]) - issues
    print(f"{data['repo']}: {issues} issues, {pulls} pull requests in {outdir}")

    saved = False
    queue = SaveQueue(outdir / "SAVES.json")
    if args.save or args.poll:
        queue.plan(plan_urls(data["repo"], data["objects"]))
        queue.save()
        if not args.no_save:
            queue.submit(client, pace=args.pace, workers=args.workers)
            saved = True
        if args.poll:
            counts = queue.poll(client, sweeps=max(args.sweeps, 1), settle=args.settle, workers=args.workers)
            print(f"capture state: {counts}")
        merge_saves(outdir, queue)
    recover_note(outdir, data["repo"], saved=saved)

    if args.zip:
        target = zip_snapshot(outdir, Path(args.zip))
        print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    workdir = Path(args.dir)
    ledger_path = workdir / "ledger.json"
    if not ledger_path.exists():
        print(f"no ledger at {ledger_path}", file=sys.stderr)
        return 1
    ledger = Ledger.load(ledger_path)
    text = render_ledger(ledger)
    (workdir / "LEDGER.md").write_text(text, encoding="utf-8")
    if args.stdout:
        print(text)
    else:
        print(f"wrote {workdir / 'LEDGER.md'}")
    return 0


def render_ledger(ledger: Ledger) -> str:
    """Render the ledger as a document a person can read cold."""
    lines = [
        f"# Ledger: {ledger.account}",
        "",
        f"Account id: {ledger.account_id if ledger.account_id else 'unknown'}",
        "",
        "This file lists what the account is known to have created, and which",
        "source proved each row. A row is evidence the thing existed. It is not",
        "evidence the content survived; that is the recovered column and the",
        "files beside this one.",
        "",
        "## What was found",
        "",
        ledger.table().rstrip(),
        "",
        "## Gaps",
        "",
    ]
    if ledger.gaps:
        for gap in ledger.gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("None recorded.")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghre",
        description="Ledger-first recovery for suspended or deleted GitHub accounts.",
    )
    parser.add_argument("--version", action="version", version=f"ghre {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # Options shared by both phases. The token and the proxy come from the
    # environment when they are not given here, so a run needs no flags.
    def network_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--token",
            default=None,
            help="GitHub token; defaults to GITHUB_TOKEN then GH_TOKEN",
        )
        p.add_argument(
            "--transport",
            default=None,
            choices=["pkgforge", "direct"],
            help=(
                "route for every request: pkgforge (default) forwards through a "
                "public broker, direct talks to GitHub and the Internet Archive; "
                "defaults to GHRE_TRANSPORT"
            ),
        )

    ledger = sub.add_parser("ledger", help="enumerate everything the account made")
    ledger.add_argument("account", help="GitHub account name")
    ledger.add_argument("--out", default=".", help="output directory")
    ledger.add_argument("--budget", type=int, default=200, help="request cap")
    ledger.add_argument("--timeout", type=int, default=45, help="per-request seconds")
    network_flags(ledger)
    ledger.add_argument(
        "--seed",
        action="append",
        default=[],
        help="repository name known by other means, comma separated",
    )
    ledger.add_argument(
        "--seed-file",
        action="append",
        default=[],
        help="file of repository names, one per line",
    )
    ledger.add_argument(
        "--org",
        action="append",
        default=[],
        help="organisation to read the org event feed for, comma separated",
    )
    ledger.set_defaults(func=cmd_ledger)

    recover = sub.add_parser("recover", help="fetch bodies for ledger entries")
    recover.add_argument("dir", help="directory holding ledger.json")
    recover.add_argument("--account", default="", help="account, if not in the ledger")
    recover.add_argument("--sha", action="append", default=[], help="commit to chase")
    recover.add_argument(
        "--repo",
        action="append",
        default=[],
        help="extra owner/repo to sweep for this account's refs",
    )
    recover.add_argument("--term", action="append", default=[], help="snippet seed")
    recover.add_argument("--budget", type=int, default=200, help="request cap")
    recover.add_argument("--timeout", type=int, default=45, help="per-request seconds")
    network_flags(recover)
    recover.add_argument("--skip-refs", action="store_true")
    recover.add_argument("--skip-archive", action="store_true")
    recover.add_argument("--skip-snippets", action="store_true")
    recover.set_defaults(func=cmd_recover)

    snapshot = sub.add_parser(
        "snapshot", help="archive a repository's issues and pull requests"
    )
    snapshot.add_argument("repo", help="owner/name, the repository to archive")
    snapshot.add_argument("--out", required=True, help="directory to write")
    network_flags(snapshot)
    snapshot.add_argument(
        "--save", action="store_true", help="ask an independent archive for a copy"
    )
    snapshot.add_argument(
        "--poll", action="store_true", help="follow the save jobs answered"
    )
    snapshot.add_argument(
        "--no-save", action="store_true", help="with --poll, only poll"
    )
    snapshot.add_argument(
        "--sweeps", type=int, default=1, help="how many polling sweeps to make"
    )
    snapshot.add_argument(
        "--settle", type=float, default=45.0, help="seconds between sweeps"
    )
    snapshot.add_argument(
        "--pace", type=float, default=0.75,
        help="seconds between submissions (sequential workers only)"
    )
    snapshot.add_argument(
        "--workers", type=int, default=4,
        help="save requests in flight at once; 1 is sequential"
    )
    snapshot.add_argument(
        "--pause", type=float, default=0.0, help="seconds between API pages"
    )
    snapshot.add_argument(
        "--zip", default="", help="also write a zip at this path"
    )
    snapshot.add_argument(
        "--verify", action="store_true", help="check an existing snapshot and stop"
    )
    snapshot.add_argument("--budget", type=int, default=5000, help="request cap")
    snapshot.add_argument("--timeout", type=int, default=45, help="per-request seconds")
    snapshot.set_defaults(func=cmd_snapshot)

    report = sub.add_parser("report", help="render ledger.json as Markdown")
    report.add_argument("dir", help="directory holding ledger.json")
    report.add_argument("--stdout", action="store_true", help="print instead of write")
    report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
