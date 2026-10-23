"""Installer cache decisions must survive retries without hiding changes."""
import importlib.metadata
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("creator_notes_install_state", ROOT / "scripts" / "install_state.py")
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


class InstallStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "downloaded source"
        self.source.mkdir()
        (self.source / "pyproject.toml").write_text('[project]\nname="creator-notes"\nversion="0.1.0"\ndependencies=["mcp==1.27.2"]\n', encoding="utf-8")
        module = self.source / "src" / "creator_notes" / "__init__.py"
        module.parent.mkdir(parents=True)
        module.write_text('__version__="0.1.0"\n', encoding="utf-8")
        self.root = self.base / "\u4e2d\u6587 installed app"
        self.python = self.root / "runtime" / ".venv" / "Scripts" / "python.exe"
        self.health = {"runtime_ready": True, "imports_ok": True, "versions": {"creator-notes": "0.1.0", "mcp": "1.27.2"}, "browser_ready": True}

    def install_receipt(self):
        staged = state.stage(self.source, self.root)
        state.record_package(self.source, self.root, self.python, self.health)
        return staged

    def test_failed_later_stage_reuses_verified_source_and_completed_package(self):
        first = self.install_receipt()
        retry = state.stage(self.source, self.root)
        self.assertTrue(retry["reused"])
        self.assertEqual(retry["app_path"], first["app_path"])
        health = dict(self.health, browser_ready=False)
        plan = state.inspect(self.source, self.root, self.python, health)
        self.assertTrue(plan["package_current"])
        self.assertFalse(plan["browser_ready"])
        self.assertEqual(len(list((self.root / "app").iterdir())), 1)

    def test_source_edit_or_dependency_drift_invalidates_the_fast_path(self):
        self.install_receipt()
        health = dict(self.health, versions={"creator-notes": "0.1.0", "mcp": "1.0.0"})
        self.assertFalse(state.inspect(self.source, self.root, self.python, health)["package_current"])
        (self.source / "src" / "creator_notes" / "__init__.py").write_text('__version__="0.1.0"\nFIXED=True\n', encoding="utf-8")
        self.assertFalse(state.inspect(self.source, self.root, self.python, self.health)["package_current"])
        self.assertFalse(state.stage(self.source, self.root)["reused"])

    def test_tampered_snapshot_cannot_be_reused_as_current_installation(self):
        first = self.install_receipt()
        (Path(first["app_path"]) / "src" / "creator_notes" / "__init__.py").write_text("changed after installation", encoding="utf-8")
        plan = state.inspect(self.source, self.root, self.python, self.health)
        self.assertFalse(plan["package_current"])
        self.assertIsNone(plan["staged_app_path"])

    def test_receipt_outside_owned_app_tree_is_not_trusted(self):
        self.install_receipt()
        receipt = self.root / "integration" / "installed-source.json"
        value = json.loads(receipt.read_text(encoding="utf-8"))
        value["app_path"] = str(self.source)
        receipt.write_text(json.dumps(value), encoding="utf-8")
        self.assertFalse(state.inspect(self.source, self.root, self.python, self.health)["package_current"])

    def test_partial_or_wrong_interpreter_state_cannot_mark_package_complete(self):
        state.stage(self.source, self.root)
        with self.assertRaises(ValueError):
            state.record_package(self.source, self.root, self.python, dict(self.health, imports_ok=False))
        self.assertFalse((self.root / "integration" / "installed-source.json").exists())
        state.record_package(self.source, self.root, self.python, self.health)
        another_python = self.python.with_name("other-python.exe")
        self.assertFalse(state.inspect(self.source, self.root, another_python, self.health)["package_current"])

    def test_corrupted_private_cache_receipt_can_be_rebuilt_without_touching_source(self):
        self.install_receipt()
        receipt = self.root / "integration" / "installed-source.json"
        receipt.write_text("interrupted private receipt", encoding="utf-8")
        self.assertFalse(state.inspect(self.source, self.root, self.python, self.health)["package_current"])
        state.record_package(self.source, self.root, self.python, self.health)
        self.assertTrue(state.inspect(self.source, self.root, self.python, self.health)["package_current"])

    @unittest.skipUnless(os.name == "nt", "Windows installer integration")
    def test_completed_install_runs_offline_without_bootstrap_or_package_reinstall(self):
        try:
            distribution = importlib.metadata.distribution("creator-notes")
            playwright_distribution = importlib.metadata.distribution("playwright")
        except importlib.metadata.PackageNotFoundError:
            self.skipTest("Installed project and browser library needed for the offline runtime fixture")
        shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not shell:
            self.skipTest("PowerShell unavailable")
        scripts = self.source / "scripts"
        scripts.mkdir()
        for name in ("install-workbuddy.ps1", "install_state.py", "configure.py", "build_release.py", "copy_source.py"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        (self.source / "pyproject.toml").write_text('[project]\nname="creator-notes"\nversion="' + distribution.version + '"\ndependencies=["playwright==' + playwright_distribution.version + '"]\n', encoding="utf-8")
        skill = self.source / "skills" / "creator-notes"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: creator-notes\ndescription: offline fixture\n---\n", encoding="utf-8")
        environment = dict(os.environ)
        environment.update({"PLAYWRIGHT_BROWSERS_PATH": str(self.root / "browsers"), "PYTHONUTF8": "1",
                            "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
                            "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT": "100"})
        venv_dir = self.root / "runtime" / ".venv"
        venv.EnvBuilder(with_pip=False).create(venv_dir)
        python = venv_dir / "Scripts" / "python.exe"
        site = venv_dir / "Lib" / "site-packages"
        # The fixture shares already-installed public dependencies, never an
        # account profile or runtime data. Its empty browser is never launched.
        application_source = Path(importlib.import_module("creator_notes").__file__).resolve().parent.parent
        paths = {str(distribution.locate_file("")), str(playwright_distribution.locate_file("")), str(application_source)}
        (site / "fixture-dependencies.pth").write_text("".join("import site; site.addsitedir(" + repr(path) + ")\n" for path in sorted(paths)), encoding="utf-8")
        probe_code = "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p: print(p.chromium.executable_path)"
        browser = Path(subprocess.check_output([str(python), "-I", "-X", "utf8", "-c", probe_code], env=environment, text=True, encoding="utf-8").strip())
        browser.parent.mkdir(parents=True, exist_ok=True)
        browser.touch()
        (self.root / ".creator-notes-install.json").write_text('{"project":"creator-notes","format":1}', encoding="utf-8")
        old_environment = {key: os.environ.get(key) for key in ("PLAYWRIGHT_BROWSERS_PATH",)}
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = environment["PLAYWRIGHT_BROWSERS_PATH"]
        try:
            state.stage(self.source, self.root)
            imported = subprocess.run([str(python), "-I", "-X", "utf8", "-c", "import creator_notes, httpx; from mcp.server.fastmcp import FastMCP"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(imported.returncode, 0, imported.stderr)
            plan = state.inspect(self.source, self.root, python)
            self.assertTrue(plan["dependencies_ready"], state.probe(python, state.requirements(self.source)))
            self.assertIsNotNone(plan["staged_app_path"], plan)
            state.record_package(self.source, self.root, python)
        finally:
            for key, value in old_environment.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value
        command = "function Invoke-WebRequest { throw 'NETWORK_NOT_ALLOWED' }; & '" + str(scripts / "install-workbuddy.ps1").replace("'", "''") + "' -InstallRoot '" + str(self.root).replace("'", "''") + "'"
        completed = subprocess.run([shell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("NETWORK_NOT_ALLOWED", completed.stdout + completed.stderr)
        self.assertIn("copying and package installation skipped", completed.stdout)
        self.assertFalse((self.root / "bootstrap" / "uv-0.13.4.exe").exists())
        self.assertEqual(list((self.root / "models").iterdir()), [])
        receipt = json.loads((self.root / "integration" / "last-install.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(receipt["status"], "ready_for_login")
        self.assertEqual(receipt["model_status"], "deferred_until_first_transcription")
        self.assertTrue(all(stage["seconds"] >= 0 and stage["status"] == "complete" for stage in receipt["stages"]))


if __name__ == "__main__":
    unittest.main()
