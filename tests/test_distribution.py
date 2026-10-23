import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent.parent


def load_script(name):
    spec = importlib.util.spec_from_file_location("creator_notes_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure = load_script("configure")
release = load_script("build_release")
copy = load_script("copy_source")


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "\u4e2d\u6587 install folder"
        self.skill = Path(self.temporary.name) / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("---\nname: creator-notes\ndescription: local creator notes\n---\n", encoding="utf-8")
        self.user = Path(self.temporary.name) / "\u6d4b\u8bd5 user" / ".codebuddy"
        self.python = self.root / "runtime" / "python.exe"

    def test_generation_does_not_register_or_copy_personal_state(self):
        (self.skill / "runtime.json").write_text('{"private":"not-for-sharing"}', encoding="utf-8")
        generated = configure.generate(self.python, self.root, self.skill)
        self.assertFalse(self.user.exists())
        config = json.loads(Path(generated["configuration"]).read_text(encoding="utf-8"))
        env = config["mcpServers"]["creator-notes"]["env"]
        self.assertEqual(env["PLAYWRIGHT_BROWSERS_PATH"], str(self.root / "browsers"))
        self.assertEqual(env["HF_HUB_DISABLE_XET"], "1")
        with zipfile.ZipFile(generated["skill"]) as archive:
            self.assertEqual(archive.namelist(), ["creator-notes/SKILL.md"])

    def test_merge_is_owned_idempotent_and_keeps_other_configuration(self):
        self.user.mkdir(parents=True)
        existing = {"other": {"retain": True}, "mcpServers": {"existing-service": {"command": "existing"}}}
        (self.user / "mcp.json").write_text(json.dumps(existing), encoding="utf-8")
        result = configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        backup = Path(result["backup"]) / "mcp.json"
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), existing)
        config_time = (self.user / "mcp.json").stat().st_mtime_ns
        backups = list((self.root / "backups").iterdir())
        repeated = configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        self.assertTrue(repeated["unchanged"])
        self.assertEqual((self.user / "mcp.json").stat().st_mtime_ns, config_time)
        self.assertEqual(list((self.root / "backups").iterdir()), backups)
        updated = json.loads((self.user / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["other"], existing["other"])
        self.assertEqual(updated["mcpServers"]["existing-service"], existing["mcpServers"]["existing-service"])
        self.assertEqual(len(updated["mcpServers"]), 2)

    def test_foreign_entry_is_rejected_without_touching_configuration(self):
        self.user.mkdir(parents=True)
        path = self.user / "mcp.json"
        original = '{"mcpServers":{"creator-notes":{"command":"another-program"}}}'
        path.write_text(original, encoding="utf-8")
        with self.assertRaises(ValueError):
            configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertFalse((self.user / "skills").exists())

    def test_foreign_skill_is_rejected_before_config_write(self):
        target = self.user / "skills" / "creator-notes"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("A user's different skill", encoding="utf-8")
        with self.assertRaises(ValueError):
            configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        self.assertFalse((self.user / "mcp.json").exists())
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "A user's different skill")

    def test_owned_receipt_does_not_allow_changed_entry_to_be_overwritten(self):
        configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        path = self.user / "mcp.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["mcpServers"]["creator-notes"]["command"] = "user-replaced-command"
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(ValueError):
            configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), config)

    def test_release_whitelist_excludes_state_and_real_reports(self):
        source = Path(self.temporary.name) / "source"
        source.mkdir()
        (source / "pyproject.toml").write_text('[project]\nversion="0.1.0"\n', encoding="utf-8")
        for relative in ("src/creator_notes/__init__.py", "skills/creator-notes/SKILL.md", ".data/storage-state.json", "reports/example.md", "scripts/runtime.json", "scripts/__pycache__/leak.py", "verification/real.py"):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("safe source or synthetic private state", encoding="utf-8")
        destination = Path(self.temporary.name) / "release"
        result = release.build(source, destination)
        self.assertEqual(result["files"], 3)
        with zipfile.ZipFile(destination / "creator-notes-0.1.0-source.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"creator-notes-0.1.0/pyproject.toml", "creator-notes-0.1.0/src/creator_notes/__init__.py", "creator-notes-0.1.0/skills/creator-notes/SKILL.md", "creator-notes-0.1.0/release-manifest.json"})

    def test_source_secret_detection_does_not_accept_concrete_token(self):
        path = Path(self.temporary.name) / "example.py"
        path.write_text("value = '" + "ghp_" + "z" * 36 + "'", encoding="utf-8")
        with self.assertRaises(ValueError):
            release.scan_text(path)

    def test_source_install_copy_never_copies_runtime_or_overlays_existing_tree(self):
        source = Path(self.temporary.name) / "downloaded source"
        source.mkdir()
        (source / "pyproject.toml").write_text('[project]\nversion="0.1.0"\n', encoding="utf-8")
        (source / ".data").mkdir()
        (source / ".data" / "storage-state.json").write_text("private state", encoding="utf-8")
        target = Path(self.temporary.name) / "\u65b0 installation"
        self.assertEqual(copy.copy_source(source, target), 1)
        self.assertTrue((target / "pyproject.toml").exists())
        self.assertFalse((target / ".data").exists())
        with self.assertRaises(ValueError):
            copy.copy_source(source, target)

    def test_unchanged_generated_files_are_not_rewritten_when_source_mtime_changes(self):
        first = configure.generate(self.python, self.root, self.skill)
        files = [Path(path) for path in first.values()]
        before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in files}
        skill_file = self.skill / "SKILL.md"
        os.utime(skill_file, (skill_file.stat().st_atime, skill_file.stat().st_mtime + 30))
        configure.generate(self.python, self.root, self.skill)
        self.assertEqual({path: (path.stat().st_mtime_ns, path.read_bytes()) for path in files}, before)

    def test_changed_skill_is_updated_with_backup_even_when_mcp_entry_is_unchanged(self):
        configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        (self.skill / "SKILL.md").write_text("---\nname: creator-notes\ndescription: revised workflow\n---\n", encoding="utf-8")
        result = configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        self.assertFalse(result["unchanged"])
        self.assertTrue((Path(result["backup"]) / "creator-notes-skill" / "SKILL.md").exists())
        self.assertEqual((self.user / "skills" / "creator-notes" / "SKILL.md").read_bytes(), (self.skill / "SKILL.md").read_bytes())

    def test_previous_owned_registration_can_receive_process_local_download_settings(self):
        configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        config_path = self.user / "mcp.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        old = config["mcpServers"]["creator-notes"]
        old["env"].pop("HF_HUB_DISABLE_XET")
        config["mcpServers"]["other-service"] = {"command": "keep-this"}
        config_path.write_text(json.dumps(config), encoding="utf-8")
        receipt_path = self.root / "integration" / "registration-receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["entry_sha256"] = configure.digest(old)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = configure.register_workbuddy(self.python, self.root, self.skill, self.user)
        self.assertFalse(result["unchanged"])
        updated = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["mcpServers"]["creator-notes"]["env"]["HF_HUB_DISABLE_XET"], "1")
        self.assertEqual(updated["mcpServers"]["other-service"], {"command": "keep-this"})


if __name__ == "__main__":
    unittest.main()
