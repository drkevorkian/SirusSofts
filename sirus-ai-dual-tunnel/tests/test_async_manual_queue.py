"""Regression: approved manual lookup runs after the HTTP job is queued."""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

GENTOO = Path(__file__).resolve().parents[1] / "gentoo"
sys.path.insert(0, str(GENTOO))
import input_server as ai


class AsyncManualQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(Path(self.directory.name) / "input.sqlite3")
        ai.init_db(self.db)
        # Do not load a model, start a tunnel, or read the real PDF corpus.
        self.engine = ai.Engine.__new__(ai.Engine)
        self.engine.db = self.db
        self.engine.gpu_percent = 90
        self.engine.knowledge = None
        self.engine.stop = threading.Event()
        self.httpd = ai.http.server.ThreadingHTTPServer(("127.0.0.1", 0), ai.Handler)
        self.httpd.engine = self.engine
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def submit(self, **updates):
        data = {"messages": [{"role": "user", "content": "2026 Summit RAVE valve?"}],
                "target": "auto", "model_id": "sirussofts-coder",
                "skip_knowledge": True, "use_manuals": True,
                "manual_query": "2026 Summit RAVE valve?"}
        data.update(updates)
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/in/jobs" % self.httpd.server_port,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_post_queues_without_synchronous_manual_lookup(self):
        with mock.patch.object(ai, "augment_manuals",
                               side_effect=AssertionError("lookup called in POST")):
            status, response = self.submit()
        self.assertEqual(status, 202)
        job = response["job_id"]
        with sqlite3.connect(self.db) as con:
            stored = con.execute(
                "SELECT state, use_manuals, manual_query, messages "
                "FROM jobs WHERE id=?", (job,)).fetchone()
        self.assertEqual(stored[:3],
                         ("queued", 1, "2026 Summit RAVE valve?"))
        self.assertEqual(json.loads(stored[3])[0]["role"], "user")

        def approved_lookup(messages, query):
            self.assertEqual(query, "2026 Summit RAVE valve?")
            return [{"role": "system", "content":
                     "LOCAL MANUAL RETRIEVAL: [M1] approved source"},
                    *messages]

        with (mock.patch.object(ai, "augment_manuals",
                                side_effect=approved_lookup) as retrieve,
              mock.patch.object(ai, "choose_worker",
                                return_value=("cpu", ai.CPU)),
              mock.patch.object(ai, "chat", return_value="Cited answer") as chat):
            self.assertTrue(self.engine.run_one())
        retrieve.assert_called_once()
        self.assertEqual(chat.call_args.args[1][0]["role"], "system")
        with sqlite3.connect(self.db) as con:
            state = con.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0]
        self.assertEqual(state, "completed")

    def test_disabled_manuals_skip_lookup(self):
        status, response = self.submit(use_manuals=False, manual_query="")
        self.assertEqual(status, 202)
        with (mock.patch.object(ai, "augment_manuals",
                                side_effect=AssertionError("not requested")),
              mock.patch.object(ai, "choose_worker",
                                return_value=("cpu", ai.CPU)),
              mock.patch.object(ai, "chat", return_value="Plain answer")):
            self.assertTrue(self.engine.run_one())

    def test_bad_manual_policy_is_rejected_without_job(self):
        for kwargs in [
            {"use_manuals": "true"}, {"use_manuals": True, "manual_query": ""},
            {"use_manuals": True, "manual_query": "x" * (ai.MAX_TEXT + 1)}
        ]:
            with self.subTest(kwargs=list(kwargs)):
                status, _ = self.submit(**kwargs)
                self.assertEqual(status, 400)
        with sqlite3.connect(self.db) as con:
            total = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
        self.assertEqual(total, 0)

    def test_legacy_queue_migrates_without_losing_jobs(self):
        legacy = str(Path(self.directory.name) / "legacy.sqlite3")
        old_id = "f" * 32
        with sqlite3.connect(legacy) as con:
            con.execute("""CREATE TABLE jobs (
                id TEXT PRIMARY KEY, messages TEXT NOT NULL, target TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, worker TEXT DEFAULT '',
                profile TEXT NOT NULL DEFAULT 'auto',
                model_id TEXT NOT NULL DEFAULT 'sirussofts-coder',
                skip_knowledge INTEGER NOT NULL DEFAULT 0,
                max_tokens INTEGER)""")
            con.execute("""INSERT INTO jobs
                (id,messages,target,state,created) VALUES(?,?,?,?,?)""",
                (old_id, '[{"role":"user","content":"Earlier question"}]',
                 "auto", "queued", 1.0))
        ai.init_db(legacy)
        with sqlite3.connect(legacy) as con:
            prior = con.execute(
                "SELECT state,use_manuals,manual_query FROM jobs WHERE id=?",
                (old_id,)).fetchone()
        self.assertEqual(prior, ("queued", 0, ""))

    def test_retrieval_failure_is_persisted_as_job_error(self):
        status, response = self.submit()
        self.assertEqual(status, 202)
        with mock.patch.object(ai, "augment_manuals",
                               side_effect=ai.RetrievalUnavailable("Private path")):
            self.assertTrue(self.engine.run_one())
        with sqlite3.connect(self.db) as con:
            state = con.execute("SELECT state FROM jobs WHERE id=?",
                                (response["job_id"],)).fetchone()[0]
        self.assertEqual(state, "error")


if __name__ == "__main__":
    unittest.main()
