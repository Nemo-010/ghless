"""Offline checks for the ledger, the diff extractor and the URL classifier.

These run without network so they can be part of a normal test run.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ghre import events as events_lane
from ghre.ledger import Ledger, LedgerEntry, classify_path
from ghre.recover import _extract_commit_diff


class LedgerTests(unittest.TestCase):
    def test_duplicate_rows_collapse(self):
        ledger = Ledger(account="someone")
        first = LedgerEntry(kind="repo", owner="someone", repo="thing")
        second = LedgerEntry(kind="repo", owner="SOMEONE", repo="THING")
        self.assertTrue(ledger.add(first))
        self.assertFalse(ledger.add(second))
        self.assertEqual(len(ledger.entries), 1)

    def test_save_and_load_round_trip(self):
        ledger = Ledger(account="someone", account_id=42)
        ledger.add(
            LedgerEntry(kind="pull", owner="other", repo="upstream", number=7)
        )
        ledger.gap("a provider was not asked")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            ledger.save(path)
            restored = Ledger.load(path)
        self.assertEqual(restored.account, "someone")
        self.assertEqual(restored.account_id, 42)
        self.assertEqual(len(restored.entries), 1)
        self.assertEqual(restored.entries[0].number, 7)
        self.assertEqual(len(restored.gaps), 1)

    def test_markdown_table_escapes_pipes(self):
        ledger = Ledger(account="someone")
        ledger.add(
            LedgerEntry(kind="repo", owner="someone", repo="a", title="x | y")
        )
        self.assertIn("x \\| y", ledger.table())


class ClassifyTests(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(classify_path("https://github.com/a/b/commit/abc"), "commit")
        self.assertEqual(classify_path("https://github.com/a/b/pull/3"), "thread")
        self.assertEqual(classify_path("https://github.com/a/b/issues/3"), "thread")
        self.assertEqual(classify_path("https://github.com/a/b/blob/main/R.md"), "file")
        self.assertEqual(classify_path("https://github.com/a/b/commits"), "commits")
        self.assertEqual(classify_path("https://github.com/a/b"), "page")


class DiffExtractionTests(unittest.TestCase):
    PAGE = """<script type="application/json" data-target="react-app.embeddedData">
{"a":{"diffLines":[{"text":"+# hello"},{"text":"+world"}],"path":"README.md","lineCount":2}}
</script>"""

    def test_extracts_added_lines(self):
        ledger = Ledger()
        with tempfile.TemporaryDirectory() as tmp:
            count = _extract_commit_diff(self.PAGE, ledger, Path(tmp), "page")
            body = (Path(tmp) / "page.files" / "README.md").read_text()
        self.assertEqual(count, 1)
        self.assertEqual(body, "# hello\nworld\n")

    def test_too_big_is_a_gap_not_an_empty_file(self):
        page = self.PAGE.replace(
            '"lineCount":2', '"lineCount":900,"isTooBig":true,"diffLines":[]'
        )
        ledger = Ledger()
        with tempfile.TemporaryDirectory() as tmp:
            count = _extract_commit_diff(page, ledger, Path(tmp), "page")
            exists = (Path(tmp) / "page.files" / "README.md").exists()
        self.assertEqual(count, 0)
        self.assertFalse(exists)
        self.assertTrue(any("not rendered" in gap for gap in ledger.gaps))


class EventIngestTests(unittest.TestCase):
    def test_events_ingest_writes_bodies(self):
        ledger = Ledger(account="someone")
        events = [
            {
                "id": "1",
                "type": "IssuesEvent",
                "created_at": "2026-01-01T00:00:00Z",
                "repo": {"name": "other/project"},
                "payload": {
                    "action": "opened",
                    "issue": {
                        "number": 7,
                        "title": "a thing is wrong",
                        "body": "the body survives here",
                        "state": "open",
                        "html_url": "https://github.com/other/project/issues/7",
                        "user": {"login": "someone"},
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                },
            },
            {
                "id": "2",
                "type": "IssueCommentEvent",
                "created_at": "2026-01-01T00:01:00Z",
                "repo": {"name": "other/project"},
                "payload": {
                    "comment": {
                        "id": 99,
                        "body": "a comment body",
                        "html_url": "https://github.com/other/project/issues/7#issuecomment-99",
                        "issue_url": "https://api.github.com/repos/other/project/issues/7",
                        "user": {"login": "someone"},
                    }
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            counts = events_lane.ingest(ledger, events, Path(tmp), "someone")
            issue_body = (
                Path(tmp) / "events" / "other__project" / "issue-7.md"
            ).read_text()
            comment_body = (
                Path(tmp) / "events" / "other__project" / "comment-99.md"
            ).read_text()
        self.assertEqual(counts["IssuesEvent"], 1)
        self.assertIn("the body survives here", issue_body)
        self.assertIn("a comment body", comment_body)
        self.assertIn("other/project", ledger.repos())

    def test_events_ingest_is_idempotent_on_rerun(self):
        ledger = Ledger(account="someone")
        events = [
            {
                "id": "1",
                "type": "IssuesEvent",
                "created_at": "2026-01-01T00:00:00Z",
                "repo": {"name": "other/project"},
                "payload": {"issue": {"number": 7, "title": "t", "body": "b"}},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            events_lane.ingest(ledger, events, Path(tmp), "someone")
            before = len(ledger.entries)
            events_lane.ingest(ledger, events, Path(tmp), "someone")
        self.assertEqual(len(ledger.entries), before)


class RouteTests(unittest.TestCase):
    def test_git_url_follows_the_transport(self):
        from ghre.broker import resolve_route

        forwarded = resolve_route("pkgforge")
        direct = resolve_route("direct")
        self.assertTrue(forwarded.uses_broker)
        self.assertFalse(direct.uses_broker)
        self.assertIn("https://github.com", forwarded.git_base)
        self.assertTrue(direct.git_base.endswith("//github.com"))

    def test_unknown_transport_refuses_rather_than_guessing(self):
        from ghre.broker import resolve_route

        with self.assertRaises(SystemExit):
            resolve_route("nonsense")


if __name__ == "__main__":
    unittest.main()
