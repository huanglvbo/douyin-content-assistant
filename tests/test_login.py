"""Session-file and launcher contracts; no real browser or login is opened."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creator_notes import login


class LoginTests(unittest.TestCase):
    def test_missing_status_does_not_claim_live_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = login.login_status(tmp)
        self.assertEqual("not_started", result["status"])
        self.assertFalse(result["storage_state_ready"])
        self.assertEqual("not_live_checked", result["session_verification"])

    def test_status_only_returns_allowlisted_fields_and_local_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "login-status.json").write_text(json.dumps({"status": "logged_in", "cookie": "DO_NOT_RETURN", "token": "secret", "qr_path": str(directory.parent / "outside.png")}), encoding="utf-8")
            (directory / "storage-state.json").write_text("{}", encoding="utf-8")
            result = login.login_status(directory)
            self.assertEqual("logged_in", result["status"])
            self.assertTrue(result["storage_state_ready"])
            self.assertNotIn("cookie", result)
            self.assertNotIn("token", result)
            self.assertNotIn("qr_path", result)

    def test_dead_helper_hides_its_expired_qr(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            image = directory / "qr.png"
            image.write_bytes(b"test")
            login._write_status(directory, "awaiting_scan", qr_path=str(image))
            with patch.object(login, "_alive", return_value=False):
                result = login.login_status(directory)
        self.assertEqual("interrupted", result["status"])
        self.assertNotIn("qr_path", result)

    def test_logged_in_without_session_is_reported_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            login._write_status(Path(tmp), "logged_in")
            result = login.login_status(tmp)
        self.assertEqual("session_missing", result["status"])

    def test_account_endpoint_needs_real_user_identity(self):
        self.assertFalse(login._account_confirmed("/aweme/v1/web/user/profile/self/", {"status_code": 0, "user": {"uid": 0}}))
        self.assertFalse(login._account_confirmed("/passport/web/get_qrcode", {"status_code": 0, "user": {"uid": 123}}))
        self.assertTrue(login._account_confirmed("/aweme/v1/web/user/profile/self/", {"status_code": 0, "user": {"uid": 123}}))

    def test_launcher_uses_current_python_and_explicit_user_data_dir(self):
        with tempfile.TemporaryDirectory(prefix="\u4e2d\u6587 \u7a7a\u683c ") as tmp, \
             patch.object(login.subprocess, "Popen", return_value=SimpleNamespace(pid=4321)) as spawn:
            result = login.begin_login(tmp, timeout=900)
            args = spawn.call_args.args[0]
            self.assertEqual("starting", result["status"])
            self.assertIn("creator_notes.login", args)
            self.assertIn(str(Path(tmp).resolve()), args)
            self.assertIn("600", args)
            self.assertEqual(login.subprocess.DEVNULL, spawn.call_args.kwargs["stdout"])
            if os.name == "nt":
                self.assertTrue(spawn.call_args.kwargs["creationflags"] & login.subprocess.CREATE_NO_WINDOW)

    def test_active_login_is_reused_without_starting_another_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            login._write_status(Path(tmp), "starting")
            with patch.object(login, "_alive", return_value=True), patch.object(login.subprocess, "Popen") as spawn:
                result = login.begin_login(tmp)
            self.assertEqual("starting", result["status"])
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
