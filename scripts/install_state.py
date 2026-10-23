"""Cache and inspect completed installer stages without network access."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import uuid


PROJECT = "creator-notes"


def _helper(name):
    spec = importlib.util.spec_from_file_location("creator_notes_install_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plain(path):
    for item in (Path(path), *Path(path).parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Installer state paths may not contain links or junctions")


def _read(path):
    _plain(path)
    if not Path(path).exists():
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _write(path, value):
    path = Path(path)
    _plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".install-", suffix=".tmp", encoding="utf-8", delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def fingerprint(source):
    release = _helper("build_release")
    rows = []
    for path, relative in release.selected_files(source):
        release.scan_text(path)
        rows.append((relative.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    if not any(name == "pyproject.toml" for name, _ in rows):
        raise ValueError("Source package has no project metadata")
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def requirements(source):
    project = tomllib.loads((Path(source) / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    result = {PROJECT: project["version"]}
    for requirement in project.get("dependencies", []):
        # Release dependencies are pinned; fail closed instead of skipping checks
        # if a future source package changes their format.
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^;\s]+)", requirement)
        if not match:
            raise ValueError("Installer cache requires exact dependency pins")
        result[match.group(1)] = match.group(2)
    return result


PROBE = r'''
import importlib.metadata as metadata
import json, pathlib, sys
expected = json.loads(sys.argv[1])
versions = {}
for name in expected:
    try: versions[name] = metadata.version(name)
    except metadata.PackageNotFoundError: versions[name] = None
browser = False
imports_ok = False
try:
    import creator_notes, httpx
    from mcp.server.fastmcp import FastMCP
    from playwright.sync_api import sync_playwright
    imports_ok = True
    with sync_playwright() as driver:
        browser = pathlib.Path(driver.chromium.executable_path).is_file()
except Exception:
    pass
print(json.dumps({"runtime_ready": sys.version_info[:2] == (3, 12),
                  "imports_ok": imports_ok, "versions": versions,
                  "browser_ready": browser}))
'''


def probe(python, expected):
    if not Path(python).is_file():
        return {"runtime_ready": False, "imports_ok": False, "versions": {}, "browser_ready": False}
    try:
        completed = subprocess.run([str(python), "-I", "-X", "utf8", "-c", PROBE, json.dumps(expected)],
                                   capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
        result = json.loads(completed.stdout)
        if isinstance(result, dict):
            return result
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return {"runtime_ready": False, "imports_ok": False, "versions": {}, "browser_ready": False}


def _snapshot(receipt, root, expected_fingerprint):
    if receipt.get("project") != PROJECT or receipt.get("source_sha256") != expected_fingerprint:
        return None
    try:
        raw = Path(receipt["app_path"])
        _plain(raw)
        path = raw.resolve()
        path.relative_to(Path(root).resolve() / "app")
        if path.is_dir() and fingerprint(path) == expected_fingerprint:
            return path
    except (KeyError, OSError, ValueError):
        pass
    return None


def inspect(source, root, python, health=None):
    source, root, python = Path(source).resolve(), Path(root).resolve(), Path(python).resolve()
    _plain(root)
    expected = requirements(source)
    current_fingerprint = fingerprint(source)
    health = probe(python, expected) if health is None else health
    dependencies_ready = bool(health.get("runtime_ready") and health.get("imports_ok") and
                              all(health.get("versions", {}).get(name) == version for name, version in expected.items()))
    installed = _read(root / "integration" / "installed-source.json")
    app = _snapshot(installed, root, current_fingerprint)
    same_python = installed.get("python") == str(python)
    stage = _read(root / "integration" / "staged-source.json")
    staged = _snapshot(stage, root, current_fingerprint)
    return {"source_sha256": current_fingerprint, "runtime_ready": bool(health.get("runtime_ready")),
            "dependencies_ready": dependencies_ready, "package_current": bool(dependencies_ready and app and same_python),
            "app_path": str(app) if app else None, "staged_app_path": str(staged) if staged else None,
            "browser_ready": bool(health.get("browser_ready"))}


def stage(source, root):
    source, root = Path(source).resolve(), Path(root).resolve()
    _plain(root)
    expected = fingerprint(source)
    state = root / "integration" / "staged-source.json"
    current = _snapshot(_read(state), root, expected)
    if current:
        return {"app_path": str(current), "source_sha256": expected, "reused": True}
    target = root / "app" / (expected[:16] + "-" + uuid.uuid4().hex[:8])
    _plain(target)
    _helper("copy_source").copy_source(source, target)
    if fingerprint(target) != expected:
        raise ValueError("Source changed during installation; retry from a stable source package")
    receipt = {"project": PROJECT, "source_sha256": expected, "app_path": str(target),
               "staged_at": datetime.now(timezone.utc).isoformat()}
    _write(state, receipt)
    return {"app_path": str(target), "source_sha256": expected, "reused": False}


def record_package(source, root, python, health=None):
    source, root, python = Path(source).resolve(), Path(root).resolve(), Path(python).resolve()
    plan = inspect(source, root, python, health)
    if not plan["dependencies_ready"] or not plan["staged_app_path"]:
        raise ValueError("Package stage has not passed its local health check")
    receipt = {"project": PROJECT, "source_sha256": plan["source_sha256"], "app_path": plan["staged_app_path"],
               "python": str(python), "completed_at": datetime.now(timezone.utc).isoformat()}
    _write(root / "integration" / "installed-source.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "stage", "record-package"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--python", type=Path)
    args = parser.parse_args()
    if args.command == "stage":
        result = stage(args.source, args.install_root)
    else:
        if args.python is None:
            parser.error("--python is required for this command")
        operation = inspect if args.command == "inspect" else record_package
        result = operation(args.source, args.install_root, args.python)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
