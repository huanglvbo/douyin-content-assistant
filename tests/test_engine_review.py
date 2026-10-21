"""Regression contracts for immutability across review and cache reuse."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from creator_notes.engine import Manager
from creator_notes.paths import AppPaths


class ControlledCollector:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def resolve_creator(self, _query):
        return {"status": "resolved", "profile": {"sec_uid": "one", "nickname": "\u535a\u4e3b"}}

    async def collect_latest(self, profile):
        return {"profile": profile, "collected_at": "2026-10-21T10:00:00+00:00", "latest": {
            "video_id": "1234567890123456789", "title": "\u539f\u59cb\u6d4b\u8bd5", "kind": "video", "create_time": 1,
            "url": "https://www.douyin.com/video/1234567890123456789",
            "statistics": {"like": 1, "comment": 2, "collect": 3, "share": 4}}}


def finished(manager, job):
    for _ in range(200):
        job = manager.report(job["check_id"])
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(.01)
    raise AssertionError("Controlled job did not finish")


class OriginalIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Manager(AppPaths(Path(self.temp.name)), collector_factory=ControlledCollector,
            acquire=lambda *_args, **_kwargs: Path("controlled.wav"),
            recognize=lambda *_args, **_kwargs: {"segments": [{"start": 0, "end": 1, "text": "\u539f\u59cb\u5185\u5bb9"}],
                "transcript": "\u539f\u59cb\u5185\u5bb9", "duration": 1.5, "model": "small"})

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def test_review_rejects_modified_original_even_if_request_uses_new_hash(self):
        job = finished(self.manager, self.manager.submit("\u535a\u4e3b"))
        source = Path(job["files"]["segments.json"])
        source.write_text(json.dumps([{"start": 0, "end": 1, "text": "\u540e\u6765\u6539\u5199\u7684\u5185\u5bb9"}], ensure_ascii=False), encoding="utf-8")
        request = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "edits": [], "pending_review": [],
            "overview": {"summary": "\u6982\u89c8", "sections": [{"start": 0, "end": 1, "title": "\u5185\u5bb9", "summary": "\u8bb2\u8ff0\u5185\u5bb9"}],
                "limitations": ["\u672a\u56de\u542c"]}}
        with self.assertRaises(ValueError):
            self.manager.review(job["check_id"], request)
        self.assertEqual({}, self.manager.store.history("one"))

    def test_repeat_does_not_copy_modified_plaintext_from_cache(self):
        first = finished(self.manager, self.manager.submit("\u535a\u4e3b"))
        Path(first["files"]["transcript.txt"]).write_text("\u540e\u6765\u6539\u5199\u7684\u5185\u5bb9", encoding="utf-8")
        second = finished(self.manager, self.manager.submit("\u535a\u4e3b"))
        if second["status"] == "done":
            self.assertEqual("\u539f\u59cb\u5185\u5bb9", Path(second["files"]["transcript.txt"]).read_text(encoding="utf-8"))
        else:
            self.assertFalse(second["complete"])


if __name__ == "__main__":
    unittest.main()
