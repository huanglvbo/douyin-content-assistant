"""Generate host configuration; optional WorkBuddy registration is explicit.

Only this project's entry and owned Skill may be changed. User state is never
copied into the installation or a shareable package.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import zipfile


PROJECT = "creator-notes"
MARKER = ".creator-notes-owned.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def write_json(path, value):
    return write_if_changed(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def write_if_changed(path, content):
    path = Path(path)
    assert_plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return False
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".creator-notes-", suffix=".tmp", delete=False) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.replace(path)
    return True


def read_json(path, default=None):
    if not Path(path).exists():
        return default
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a JSON object; existing file was not changed")
    return value


def assert_plain_path(path):
    for item in (Path(path), *Path(path).parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Configuration paths may not contain links or junctions")


def entry_for(python, install_root):
    root = Path(install_root).resolve()
    return {"command": str(Path(python).resolve()), "args": ["-m", "creator_notes.server"],
            "env": {"CREATOR_NOTES_HOME": str(root), "CREATOR_NOTES_MODELS": str(root / "models"),
                    "PLAYWRIGHT_BROWSERS_PATH": str(root / "browsers"), "PYTHONUTF8": "1", "HF_HUB_DISABLE_XET": "1"}}


def skill_files(skill_source):
    source = Path(skill_source).resolve()
    for path in sorted(source.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Skill source contains a symbolic link")
        if path.is_file() and (path.name == "SKILL.md" or (path.parts[len(source.parts)] in ("references", "agents") and path.suffix in (".md", ".yaml", ".yml"))):
            yield path, path.relative_to(source)


def generate(python, install_root, skill_source):
    assert_plain_path(install_root)
    root = Path(install_root).resolve()
    output = root / "integration"
    assert_plain_path(output)
    output.mkdir(parents=True, exist_ok=True)
    entry = entry_for(python, root)
    write_json(output / "workbuddy-mcp.json", {"mcpServers": {PROJECT: entry}})
    # JSON string literals are also valid TOML basic strings for these paths.
    fragment = "[mcp_servers.creator-notes]\ncommand = " + json.dumps(entry["command"]) + "\nargs = [\"-m\", \"creator_notes.server\"]\n\n[mcp_servers.creator-notes.env]\n"
    fragment += "".join(key + " = " + json.dumps(value) + "\n" for key, value in entry["env"].items())
    write_if_changed(output / "codex-mcp.toml", fragment.encode("utf-8"))
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, relative in skill_files(skill_source):
            info = zipfile.ZipInfo((Path(PROJECT) / relative).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    write_if_changed(output / "creator-notes-skill.zip", payload.getvalue())
    return {"configuration": str(output / "workbuddy-mcp.json"), "skill": str(output / "creator-notes-skill.zip"), "codex_fragment": str(output / "codex-mcp.toml")}


def register_workbuddy(python, install_root, skill_source, user_dir):
    """Opt-in merge into the documented .codebuddy directory, with backups."""
    root = Path(install_root).resolve()
    user_dir = Path(user_dir).resolve()
    config_path = user_dir / "mcp.json"
    skill_target = user_dir / "skills" / PROJECT
    for target in (install_root, user_dir, config_path, skill_target):
        assert_plain_path(target)
    if skill_target.exists():
        for target in skill_target.rglob("*"):
            assert_plain_path(target)
    receipt_path = root / "integration" / "registration-receipt.json"
    entry = entry_for(python, root)
    config = read_json(config_path, {})
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("Existing mcpServers is not an object; nothing was changed")
    receipt = read_json(receipt_path, {})
    existing = servers.get(PROJECT)
    if existing is not None and existing != entry:
        if not (receipt.get("user_dir") == str(user_dir) and receipt.get("entry_sha256") == digest(existing)):
            raise ValueError("A different creator-notes MCP entry already exists; nothing was changed")
    if skill_target.exists():
        marker = read_json(skill_target / MARKER, {})
        if marker.get("project") != PROJECT or marker.get("install_root") != str(root):
            raise ValueError("A different creator-notes Skill already exists; nothing was changed")
    files = list(skill_files(skill_source))
    if not any(relative.as_posix() == "SKILL.md" for _, relative in files):
        raise ValueError("Skill source is incomplete; nothing was changed")
    same_skill = skill_target.exists() and all((skill_target / relative).is_file() and
                                               (skill_target / relative).read_bytes() == path.read_bytes()
                                               for path, relative in files)
    if (existing == entry and same_skill and receipt.get("user_dir") == str(user_dir) and
            receipt.get("entry_sha256") == digest(entry)):
        return {"config": str(config_path), "skill": str(skill_target), "backup": None, "unchanged": True}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backups = root / "backups" / stamp
    if config_path.exists():
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, backups / "mcp.json")
    if skill_target.exists():
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_target, backups / "creator-notes-skill")
    # Install the owned Skill before the configuration references its tools.
    for path, relative in files:
        destination = skill_target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    write_json(skill_target / MARKER, {"project": PROJECT, "install_root": str(root)})
    servers[PROJECT] = entry
    config["mcpServers"] = servers
    write_json(config_path, config)
    write_json(receipt_path, {"project": PROJECT, "user_dir": str(user_dir), "entry_sha256": digest(entry), "registered_at": stamp})
    return {"config": str(config_path), "skill": str(skill_target), "backup": str(backups) if backups.exists() else None, "unchanged": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--skill-source", required=True)
    parser.add_argument("--register-workbuddy", action="store_true")
    parser.add_argument("--user-dir", default=str(Path.home() / ".codebuddy"))
    args = parser.parse_args()
    generated = generate(args.python, args.install_root, args.skill_source)
    if args.register_workbuddy:
        generated["registration"] = register_workbuddy(args.python, args.install_root, args.skill_source, args.user_dir)
    print(json.dumps(generated, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
