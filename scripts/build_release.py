"""Build a small source/Skill ZIP from an explicit source allowlist."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
import zipfile


ROOT_FILES = {"pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore"}
ROOT_SUFFIXES = {"src": {".py"}, "scripts": {".py", ".ps1", ".cmd"}, "tests": {".py"}, "skills": {".md", ".yaml", ".yml"}, "docs": {".md"}}
DENIED_NAMES = {".data", ".venv", "__pycache__", ".git", ".env", "runtime.json", "storage-state.json", "node_modules", "verification", "reports", "models", "output", ".local-test"}


def selected_files(root):
    root = Path(root).resolve()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in DENIED_NAMES for part in relative.parts):
            continue
        if (path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())) and (relative.parts[0] in ROOT_SUFFIXES or relative.name in ROOT_FILES):
            raise ValueError("Release source contains a link or junction: " + relative.as_posix())
        allowed = (len(relative.parts) == 1 and relative.name in ROOT_FILES) or (len(relative.parts) > 1 and relative.parts[0] in ROOT_SUFFIXES and path.suffix in ROOT_SUFFIXES[relative.parts[0]])
        if not allowed:
            continue
        if path.is_file():
            yield path, relative


def scan_text(path):
    value = Path(path).read_text(encoding="utf-8-sig")
    # These patterns match concrete credentials, not names in documentation.
    if re.search(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-[A-Za-z0-9]{32,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)", value):
        raise ValueError("Possible credential in source: " + Path(path).name)
    if re.search(r"[A-Za-z]:[\\/](?:AI|Users|Documents and Settings)[\\/]", value, re.IGNORECASE):
        raise ValueError("Developer-specific absolute path in source: " + Path(path).name)


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z0-9.-]+)?", version):
        raise ValueError("Unsupported release version")
    files = list(selected_files(root))
    for path, _ in files:
        scan_text(path)
    manifest = {relative.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path, relative in files}
    output.mkdir(parents=True, exist_ok=True)
    source_zip = output / f"creator-notes-{version}-source.zip"
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        prefix = f"creator-notes-{version}/"
        for path, relative in files:
            archive.write(path, prefix + relative.as_posix())
        archive.writestr(prefix + "release-manifest.json", json.dumps({"version": version, "files": manifest}, ensure_ascii=False, indent=2) + "\n")
    skill_zip = output / f"creator-notes-{version}-skill.zip"
    with zipfile.ZipFile(skill_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, relative in files:
            if relative.parts[:2] == ("skills", "creator-notes"):
                archive.write(path, Path(*relative.parts[1:]).as_posix())
    sums = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (source_zip, skill_zip)}
    (output / "SHA256SUMS.txt").write_text("".join(f"{checksum}  {name}\n" for name, checksum in sums.items()), encoding="utf-8")
    return {"version": version, "files": len(files), "artifacts": sums}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output or args.root / "dist"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
