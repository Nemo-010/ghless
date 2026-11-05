"""The ledger: everything known about an account, before anything is recovered.

A recovery run has two phases and they must not be mixed. The ledger phase
lists what existed: the repositories, their pull requests, their issues, their
commits, the account itself. The recovery phase then tries to obtain bodies
for the entries the ledger recorded. Keeping them apart means a failed
recovery never erases the evidence that a thing existed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LedgerEntry:
    """One item the account is known to have created or touched."""

    kind: str  # repo, pull, issue, commit, profile, gist
    owner: str
    repo: str = ""
    number: int | None = None
    title: str = ""
    sha: str = ""
    state: str = ""
    created: str = ""
    url: str = ""
    source: str = ""  # which evidence produced this row
    recovered: bool = False
    local_path: str = ""
    note: str = ""

    def key(self) -> str:
        return "|".join(
            [
                self.kind,
                self.owner.lower(),
                self.repo.lower(),
                str(self.number or ""),
                self.sha.lower(),
            ]
        )


@dataclass
class Ledger:
    account: str = ""
    account_id: int | None = None
    entries: list[LedgerEntry] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set, repr=False)

    def add(self, entry: LedgerEntry) -> bool:
        """Record an entry. False when it was already known."""
        key = entry.key()
        if key in self._seen:
            return False
        self._seen.add(key)
        self.entries.append(entry)
        return True

    def gap(self, text: str) -> None:
        if text not in self.gaps:
            self.gaps.append(text)

    def fact(self, text: str) -> None:
        """A positive observation about the run, kept out of the gaps list."""
        if text not in self.facts:
            self.facts.append(text)

    def of_kind(self, kind: str) -> list[LedgerEntry]:
        return [e for e in self.entries if e.kind == kind]

    def repos(self) -> list[str]:
        names = []
        for entry in self.entries:
            if entry.kind == "repo" and entry.repo:
                name = f"{entry.owner}/{entry.repo}"
                if name not in names:
                    names.append(name)
        return names

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "account": self.account,
            "account_id": self.account_id,
            "counts": {
                k: len(self.of_kind(k))
                for k in sorted({e.kind for e in self.entries}) or ["repo"]
            },
            "gaps": self.gaps,
            "facts": self.facts,
            "entries": [vars(e) for e in self.entries],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        payload = json.loads(path.read_text(encoding="utf-8"))
        ledger = cls(
            account=payload.get("account", ""),
            account_id=payload.get("account_id"),
        )
        ledger.gaps = list(payload.get("gaps", []))
        ledger.facts = list(payload.get("facts", []))
        for row in payload.get("entries", []):
            known = {f for f in LedgerEntry.__dataclass_fields__}
            ledger.add(LedgerEntry(**{k: v for k, v in row.items() if k in known}))
        return ledger

    def table(self) -> str:
        """A Markdown table of the ledger, grouped by kind."""
        lines = []
        order = ["profile", "repo", "pull", "issue", "commit", "gist", "other"]
        kinds = sorted(
            {e.kind for e in self.entries},
            key=lambda k: order.index(k) if k in order else len(order),
        )
        for kind in kinds:
            rows = self.of_kind(kind)
            lines.append(f"### {kind} ({len(rows)})")
            lines.append("")
            lines.append("| repo | # | sha | state | created | title | recovered |")
            lines.append("|---|---|---|---|---|---|---|")
            for e in rows:
                repo = f"{e.owner}/{e.repo}" if e.repo else e.owner
                lines.append(
                    "| {} | {} | {} | {} | {} | {} | {} |".format(
                        _cell(repo),
                        e.number or "",
                        _cell(e.sha[:12]),
                        _cell(e.state),
                        _cell(e.created[:10]),
                        _cell(e.title),
                        "yes" if e.recovered else "no",
                    )
                )
            lines.append("")
        return "\n".join(lines)


def _cell(value: str) -> str:
    """Escape a value for a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def classify_path(path: str) -> str:
    """Guess what a captured URL is, from its shape.

    Used only to order the recovery queue: archive rows are fetched cheapest
    first, and a README is worth more than an assets page.
    """
    low = path.lower()
    if "/commit/" in low:
        return "commit"
    if "/pull/" in low or "/issues/" in low:
        return "thread"
    if "/blob/" in low:
        return "file"
    if "/commits" in low:
        return "commits"
    if low.endswith(".md") or "/raw/" in low:
        return "file"
    return "page"
