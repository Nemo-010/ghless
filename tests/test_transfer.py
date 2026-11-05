"""Offline checks for gh-forge's transfer helpers: names, short reads, clone safety."""
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "components", "gh-forge"))
from ghforge.cmds import transfer  # noqa: E402
from ghforge.errors import GhError  # noqa: E402


class Short(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "1000")
        self.end_headers()
        self.wfile.write(b"x" * 11)
        self.close_connection = True

    def log_message(self, *a):
        pass


class TransferTests(unittest.TestCase):
    def test_hostile_asset_names_are_refused(self):
        for bad in ("../x", "/tmp/x", "a/b", "..", ""):
            with self.assertRaises(GhError, msg=bad):
                transfer._safe_name(bad)
        self.assertEqual(transfer._safe_name("ok.tar.gz"), "ok.tar.gz")

    def test_short_body_is_an_error_and_leaves_no_file(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), Short)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = transfer.BROKER
        transfer.BROKER = "http://127.0.0.1:%d" % srv.server_port
        try:
            with tempfile.TemporaryDirectory() as d:
                dest = os.path.join(d, "f")
                with self.assertRaises(GhError):
                    transfer._download(["http://127.0.0.1:%d/f" % srv.server_port], dest)
                self.assertEqual(os.listdir(d), [])
        finally:
            transfer.BROKER = old
            srv.shutdown()

    def test_clone_into_non_empty_directory_keeps_it(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "pre"))
            open(os.path.join(d, "pre", "mine.txt"), "w").write("keep")
            with self.assertRaises(GhError):
                transfer.repo_clone(["octocat/Hello-World", os.path.join(d, "pre")], None)
            self.assertTrue(os.path.exists(os.path.join(d, "pre", "mine.txt")))


if __name__ == "__main__":
    unittest.main()
