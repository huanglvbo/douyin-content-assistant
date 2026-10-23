"""Release privacy checks use entirely synthetic protected values."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import sys


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_public_tree.py"
SPEC = importlib.util.spec_from_file_location("check_public_tree_test_subject", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECK
SPEC.loader.exec_module(CHECK)


class PublicTreeTests(unittest.TestCase):
    def test_protected_content_and_path_are_reported_without_values(self):
        denylist = {"private_identity": ("FixtureCreator",), "private_video": ("9876543210987654321",)}
        findings = CHECK.inspect_bytes("docs/FixtureCreator.md", b"FixtureCreator\n9876543210987654321", denylist)
        rendered = json.dumps([CHECK.asdict(item) for item in findings])
        self.assertNotIn("FixtureCreator", rendered)
        self.assertNotIn("9876543210987654321", rendered)
        self.assertIn("[redacted]", rendered)
        self.assertEqual({item.category for item in findings}, set(denylist))

    def test_protected_match_is_case_insensitive_with_word_boundaries(self):
        result = CHECK.inspect_bytes("README.md", b"fixturecreator\nFixtureCreatorExtra", {"private_identity": ("FixtureCreator",)})
        self.assertEqual([(item.category, item.line) for item in result], [("private_identity", 1)])

    def test_credentials_and_developer_paths_are_not_echoed(self):
        token = "ghp_" + "x" * 32
        value = token + "\n" + "C:" + chr(92) + "Users" + chr(92) + "FixtureUser" + chr(92) + "private"
        findings = CHECK.inspect_bytes("README.md", value.encode())
        self.assertEqual({item.category for item in findings}, {"credential_material", "developer_absolute_path"})
        self.assertNotIn(token, repr(findings))
        self.assertNotIn("FixtureUser", repr(findings))
        encoded_path = "C:" + chr(92) * 2 + "Users" + chr(92) * 2 + "FixtureUser"
        self.assertEqual([item.category for item in CHECK.inspect_bytes("src/fixture.py", encoded_path.encode())],
                         ["developer_absolute_path"])

    def test_public_url_blocked_but_synthetic_fixture_url_is_warning(self):
        url = b"https://www.douyin.com/video/9876543210987654321"
        public = CHECK.inspect_bytes("README.md", url)
        fixture = CHECK.inspect_bytes("tests/test_fixture.py", url)
        strict = CHECK.inspect_bytes("tests/test_fixture.py", url, strict_fixture_urls=True)
        self.assertEqual([item.severity for item in public], ["error"])
        self.assertEqual([item.severity for item in fixture], ["warning"])
        self.assertEqual([item.severity for item in strict], ["error"])

    def test_denylist_still_blocks_a_fixture_url(self):
        result = CHECK.inspect_bytes("tests/test_fixture.py", b"https://www.douyin.com/video/9876543210987654321",
                                     {"private_video": ("9876543210987654321",)})
        self.assertEqual({item.category: item.severity for item in result},
                         {"private_video": "error", "concrete_platform_url": "warning"})

    def test_runtime_files_and_archive_traversal_are_rejected(self):
        for name in ("source/reports/result.md", "source/storage-state.json", "source/media/video.mp4", "../escape.md"):
            with self.subTest(name=name):
                self.assertTrue(CHECK.inspect_bytes(name, b"{}"))
        self.assertEqual(CHECK.inspect_bytes("src/creator_notes/schema.py", b"COOKIE_FIELD = 'sessionid'"), [])

    def test_non_english_public_text_is_rejected_without_echo(self):
        result = CHECK.inspect_bytes("README.md", "\u8bf4\u660e".encode())
        self.assertEqual([(item.category, item.line) for item in result], [("non_english_cjk_text", 1)])
        self.assertNotIn("\u8bf4\u660e", repr(result))

    def test_denylist_schema_fails_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            for invalid in (["secret"], {"bad category": ["secret"]}, {"category": [None]}, {"category": ["x"]}):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Invalid private denylist schema"):
                    CHECK.load_denylist(path)
            path.write_text(json.dumps({"fixture": ["private-value"]}), encoding="utf-8")
            self.assertEqual(CHECK.load_denylist(path), {"fixture": ("private-value",)})

    def test_sensitive_filename_is_redacted_and_custom_category_cannot_disable_builtin(self):
        token = "ghp_" + "x" * 32
        result = CHECK.inspect_bytes("docs/" + token + ".md", token.encode(),
                                     {"credential_material": ("different-fixture-value",)})
        self.assertEqual([(item.category, item.line) for item in result],
                         [("credential_material", 0), ("credential_material", 1)])
        self.assertNotIn(token, repr(result))

    def test_zip_checks_each_entry_and_returns_failure_without_content(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "source.zip"
            denylist = Path(directory) / "private.json"
            denylist.write_text(json.dumps({"private_identity": ["FixtureCreator"]}), encoding="utf-8")
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("source/README.md", "FixtureCreator")
                bundle.writestr("source/reports/speech.md", "Collected speech")
            output = io.StringIO()
            with redirect_stdout(output):
                code = CHECK.main(["--zip", str(archive), "--denylist", str(denylist)])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(report["files"], 2)
            self.assertEqual(report["errors"], 2)
            self.assertNotIn("FixtureCreator", output.getvalue())
            self.assertNotIn("Collected speech", output.getvalue())

    def test_clean_source_allowlist_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "README.md").write_text("# Creator content assistant\nUse <creator-id>.\n", encoding="utf-8")
            (root / "scripts/build_release.py").write_text(
                "from pathlib import Path\ndef selected_files(root):\n    yield Path(root) / 'README.md', Path('README.md')\n", encoding="utf-8")
            count, findings = CHECK.check_tree(root)
            self.assertEqual(count, 1)
            self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
