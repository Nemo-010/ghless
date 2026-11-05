"""Offline checks for the snapshot lane.

A snapshot is the one thing here that runs before a loss, so the parts that
must not silently rot are the rendering, the hashes and the resumable state.
None of these touch the network: the transport is faked at the two methods the
lane uses.
"""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ghre.broker import Route
from ghre.snapshot import (
    SaveQueue,
    merge_saves,
    plan_urls,
    recover_note,
    render_object,
    snapshot_repo,
    verify,
    zip_snapshot,
)


def issue(number, title="a title", body="a body", pull=False):
    obj = {
        "number": number,
        "title": title,
        "state": "open",
        "user": {"login": "someone"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "comments": 0,
        "labels": [],
        "body": body,
        "html_url": f"https://github.com/o/r/issues/{number}",
    }
    if pull:
        obj["pull_request"] = {"url": "x"}
    return obj


class FakeClient:
    """The two methods the lane calls, answered from a dict of fixtures."""

    def __init__(self, lists, objects, save_answers=None):
        self.lists = lists
        self.objects = objects
        self.save_answers = save_answers or []
        self.posted = []
        self.route = Route(
            name="pkgforge",
            api_base="https://example.invalid/github",
            git_base="https://example.invalid/git",
            archive_base="https://example.invalid/archive",
            uses_broker=True,
        )

    def github(self, path, lane="api"):
        return self.objects.get(path)

    def github_list(self, path, lane="api", pause=0.0):
        return self.lists.get(path, [])

    def post(self, url, lane="save", timeout=None):
        self.posted.append(url)
        answer = self.save_answers.pop(0) if self.save_answers else (202, {})
        status = answer[0]
        job = f"spn2-{len(self.posted)}" if status == 202 else None
        body = json.dumps({"job": job}).encode() if job else b"refused"
        return status, {}, body

    def get_json(self, url, lane="save", timeout=None):
        self.posted.append(url)
        return {"status": "success", "timestamp": "20260101000000",
                "capture": "https://example.invalid/capture/20260101000000/x"}


class RenderTests(unittest.TestCase):
    def test_body_comments_reviews_and_line_comments_are_all_kept(self):
        text = render_object(
            "o/r",
            issue(7),
            [{"user": {"login": "b"}, "created_at": "t", "body": "comment body"}],
            None,
            [{"user": {"login": "c"}, "submitted_at": "t", "state": "CHANGES_REQUESTED",
              "body": "review body"}],
            [{"user": {"login": "d"}, "created_at": "t", "path": "src/x.rs",
              "line": 12, "body": "line comment"}],
        )
        for expected in ("a body", "comment body", "review body", "line comment",
                         "src/x.rs:12", "CHANGES_REQUESTED"):
            self.assertIn(expected, text)

    def test_a_deleted_author_is_named_rather_than_crashing(self):
        obj = issue(8)
        obj["user"] = None
        text = render_object("o/r", obj)
        self.assertIn("(deleted)", text)

    def test_an_empty_body_says_so_instead_of_rendering_nothing(self):
        text = render_object("o/r", issue(9, body=""))
        self.assertIn("_(empty)_", text)

    def test_pull_metadata_is_carried_when_present(self):
        text = render_object(
            "o/r", issue(10, pull=True),
            pull={"merged": True, "merged_at": "m", "additions": 3,
                  "deletions": 1, "changed_files": 2,
                  "base": {"ref": "main"}, "head": {"ref": "topic"}},
        )
        self.assertIn("merged: True", text)
        self.assertIn("base: main  head: topic", text)
        self.assertIn("+3 -1 in 2 files", text)


class SnapshotTests(unittest.TestCase):
    def build(self, tmp):
        client = FakeClient(
            lists={
                "repos/o/r/issues/1/comments": [
                    {"user": {"login": "b"}, "created_at": "t", "body": "hi"}
                ],
                "repos/o/r/issues/2/comments": [],
                "repos/o/r/issues/2/reviews": [],
                "repos/o/r/issues/2/comments": [],
            },
            objects={
                "repos/o/r": {"full_name": "o/r"},
                "repos/o/r/pulls/2": {"merged": False, "additions": 1,
                                      "deletions": 0, "changed_files": 1,
                                      "base": {"ref": "main"},
                                      "head": {"ref": "topic"}},
            },
        )
        client.lists["repos/o/r/issues?state=all"] = [issue(1), issue(2, pull=True)]
        out = Path(tmp) / "snap"
        data = snapshot_repo(client, out, "o", "r")
        return out, data

    def test_snapshot_writes_both_kinds_and_indexes_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, data = self.build(tmp)
            self.assertEqual(data["count"], 2)
            self.assertTrue((out / "issues" / "1.md").exists())
            self.assertTrue((out / "prs" / "2.md").exists())
            self.assertTrue((out / "issues" / "1.comments.json").exists())
            self.assertTrue((out / "prs" / "2.pr.json").exists())
            index = (out / "INDEX.md").read_text()
            self.assertIn("1 issues", index)
            self.assertIn("1 pull requests", index)

    def test_verify_passes_on_a_fresh_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self.build(tmp)
            self.assertEqual(verify(out), [])

    def test_verify_catches_an_edited_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self.build(tmp)
            (out / "issues" / "1.md").write_text("tampered\n")
            problems = verify(out)
            self.assertTrue(any("hash mismatch" in p for p in problems))

    def test_verify_catches_a_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self.build(tmp)
            (out / "prs" / "2.md").unlink()
            problems = verify(out)
            self.assertTrue(any("missing rendering" in p for p in problems))

    def test_plan_urls_keeps_repo_pages_and_deduplicates(self):
        urls = plan_urls("o/r", [{"url": "https://github.com/o/r/issues/1"},
                                 {"url": "https://github.com/o/r/issues/1"}])
        self.assertEqual(urls[0], "https://github.com/o/r")
        self.assertIn("https://github.com/o/r/issues?q=is%3Aissue", urls)
        self.assertEqual(urls.count("https://github.com/o/r/issues/1"), 1)

    def test_recover_note_states_whether_captures_were_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self.build(tmp)
            recover_note(out, "o/r", saved=True)
            self.assertIn("Save Page Now", (out / "RECOVER.md").read_text())


class SaveQueueTests(unittest.TestCase):
    def test_state_round_trips_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SAVES.json"
            queue = SaveQueue(path)
            queue.plan(["https://github.com/o/r/issues/1"])
            queue.save()

            resumed = SaveQueue(path)
            self.assertEqual(resumed.state["urls"], ["https://github.com/o/r/issues/1"])

            client = FakeClient(lists={}, objects={})
            self.assertEqual(resumed.submit(client, pace=0), 1)
            self.assertEqual(len(resumed.submitted()), 1)
            self.assertIn("/save?url=", client.posted[0])

    def test_a_second_submit_asks_nothing_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SAVES.json"
            queue = SaveQueue(path)
            queue.plan(["https://github.com/o/r/issues/1"])
            client = FakeClient(lists={}, objects={})
            queue.submit(client, pace=0)
            queue.submit(client, pace=0)
            self.assertEqual(len(client.posted), 1)

    def test_pooled_submit_and_poll_behave_like_the_sequential_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SAVES.json"
            queue = SaveQueue(path)
            urls = [f"https://github.com/o/r/issues/{n}" for n in range(1, 6)]
            queue.plan(urls)
            client = FakeClient(lists={}, objects={})
            queue.submit(client, pace=0, workers=3)
            self.assertEqual(len(queue.submitted()), 5)
            self.assertEqual(len(client.posted), 5)
            counts = queue.poll(client, sweeps=1, settle=0, workers=3)
            self.assertEqual(counts.get("success"), 5)
            for url in urls:
                row = queue.jobs[url]
                self.assertTrue(row["capture"])
                self.assertTrue(row["timestamp"])

    def test_save_is_atomic_and_leaves_no_temporary_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SAVES.json"
            queue = SaveQueue(path)
            queue.plan(["https://github.com/o/r/issues/1"])
            client = FakeClient(lists={}, objects={})
            queue.submit(client, pace=0, workers=2)
            data = json.loads(path.read_text())
            self.assertEqual(len(data["urls"]), 1)
            leftovers = [p.name for p in Path(tmp).iterdir() if p.name != "SAVES.json"]
            self.assertEqual(leftovers, [], "a temporary state file was left behind")

    def test_a_refusal_is_recorded_rather_than_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = SaveQueue(Path(tmp) / "SAVES.json")
            queue.plan(["https://github.com/o/r/issues/1"])
            client = FakeClient(lists={}, objects={}, save_answers=[(400, {})])
            queue.submit(client, pace=0)
            row = queue.jobs["https://github.com/o/r/issues/1"]
            self.assertIsNone(row["job"])
            self.assertEqual(row["http_status"], 400)
            self.assertTrue(row["error"])

    def test_poll_records_the_capture_and_the_summary_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = SaveQueue(Path(tmp) / "SAVES.json")
            queue.plan(["https://github.com/o/r/issues/1"])
            client = FakeClient(lists={}, objects={})
            queue.submit(client, pace=0)
            counts = queue.poll(client, sweeps=1, settle=0)
            self.assertEqual(counts.get("success"), 1)
            self.assertIn("timestamp", queue.jobs["https://github.com/o/r/issues/1"])

    def test_merge_saves_folds_capture_state_into_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            out.mkdir()
            (out / "MANIFEST.json").write_text(json.dumps({
                "repo": "o/r", "count": 1,
                "objects": [{"number": 1, "url": "https://github.com/o/r/issues/1"}],
            }))
            queue = SaveQueue(Path(tmp) / "SAVES.json")
            queue.plan(["https://github.com/o/r/issues/1"])
            queue.jobs["https://github.com/o/r/issues/1"] = {
                "job": "spn2-x", "status": "success",
                "capture": "https://example.invalid/c/1", "timestamp": "20260101000000",
            }
            merge_saves(out, queue)
            data = json.loads((out / "MANIFEST.json").read_text())
            self.assertEqual(data["objects"][0]["capture"],
                             "https://example.invalid/c/1")
            self.assertTrue((out / "SAVES.json").exists())


class ZipTests(unittest.TestCase):
    def test_zip_holds_the_manifest_and_every_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            out.mkdir()
            (out / "MANIFEST.json").write_text("{}")
            (out / "issues").mkdir()
            (out / "issues" / "1.md").write_text("x")
            target = zip_snapshot(out, Path(tmp) / "a.zip")
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.endswith("MANIFEST.json") for name in names))
            self.assertTrue(any(name.endswith("issues/1.md") for name in names))


if __name__ == "__main__":
    unittest.main()
