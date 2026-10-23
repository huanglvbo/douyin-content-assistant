"""Check release source and ZIPs without disclosing matched private values.

An optional, ignored JSON denylist maps category names to protected strings.
Never commit that denylist. Findings contain paths, categories and line numbers
only. This check supplements human review; it cannot prove absence of all PII.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


PRIVATE_PARTS = {".git", ".local-test", ".venv", ".data", "__pycache__", "node_modules",
                 "reports", "models", "verification", "output", "browser-profile", "session"}
PRIVATE_NAMES = {"storage-state.json", "runtime.json", ".env", "report.json", "review.json",
                 "segments.json", "transcript.txt", "transcript.srt", "cookies.json"}
PRIVATE_SUFFIXES = {".mp4", ".mp3", ".wav", ".m4a", ".sqlite3", ".db", ".log", ".png", ".jpg", ".jpeg"}
PATTERNS = {
    "credential_material": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-[A-Za-z0-9]{32,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "developer_absolute_path": re.compile(r"[A-Za-z]:[\\/]+(?:AI|Users|Documents and Settings)[\\/]+|/(?:Users|home)/[^/\s]+/", re.I),
    "concrete_platform_url": re.compile(r"https?://(?:www\.)?douyin\.com/(?:user|video|note)/[A-Za-z0-9_=-]{6,}|https?://v\.douyin\.com/[A-Za-z0-9_-]{5,}", re.I),
    "non_english_cjk_text": re.compile(r"[\u3400-\u9fff]"),
}


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    line: int
    severity: str = "error"


def load_denylist(path):
    if path is None:
        return {}
    try:
        content = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("The private denylist could not be read") from None
    if not isinstance(content, dict):
        raise ValueError("Invalid private denylist schema")
    count = 0
    for category, values in content.items():
        if (not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category)
                or not isinstance(values, list)
                or any(not isinstance(value, str) or not 3 <= len(value) <= 4096 for value in values)):
            raise ValueError("Invalid private denylist schema")
        count += len(values)
    if count > 10000:
        raise ValueError("The private denylist exceeds the safety limit")
    return {category: tuple(set(values)) for category, values in content.items()}


def protected_patterns(denylist):
    return {category: re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(
        re.escape(value) for value in sorted(values, key=len, reverse=True)) + r")(?![A-Za-z0-9])", re.I)
        for category, values in denylist.items() if values}


def safe_path(name, denylist):
    result = str(name).replace("\\", "/")
    for values in denylist.values():
        for value in sorted(values, key=len, reverse=True):
            result = re.sub(re.escape(value), "[redacted]", result, flags=re.I)
    for category, pattern in PATTERNS.items():
        if category != "non_english_cjk_text":
            result = pattern.sub("[redacted]", result)
    return result


def inspect_bytes(name, data, denylist=None, *, strict_fixture_urls=False):
    denylist = denylist or {}
    name = str(name).replace("\\", "/")
    public_name = safe_path(name, denylist)
    path = PurePosixPath(name)
    findings = []
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", name):
        findings.append(Finding(public_name, "unsafe_artifact_path", 0))
    if (set(path.parts).intersection(PRIVATE_PARTS) or path.name.lower() in PRIVATE_NAMES
            or path.suffix.lower() in PRIVATE_SUFFIXES):
        findings.append(Finding(public_name, "runtime_or_collected_artifact", 0))
    for category, pattern in PATTERNS.items():
        if category != "non_english_cjk_text" and pattern.search(name):
            findings.append(Finding(public_name, category, 0))
    for category, pattern in protected_patterns(denylist).items():
        if pattern.search(name):
            findings.append(Finding(public_name, category, 0))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        findings.append(Finding(public_name, "unexpected_binary_artifact", 0))
        return findings
    fixture = "tests" in path.parts and path.suffix == ".py"
    content_patterns = list(PATTERNS.items()) + list(protected_patterns(denylist).items())
    for number, line in enumerate(text.splitlines(), 1):
        for category, pattern in content_patterns:
            if pattern.search(line):
                severity = "warning" if category == "concrete_platform_url" and fixture and not strict_fixture_urls else "error"
                findings.append(Finding(public_name, category, number, severity))
    return findings


def check_tree(root, denylist=None, *, strict_fixture_urls=False):
    root = Path(root).resolve()
    builder_path = root / "scripts" / "build_release.py"
    try:
        spec = importlib.util.spec_from_file_location("release_source_allowlist", builder_path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        files = list(builder.selected_files(root))
    except (OSError, ValueError, AttributeError):
        raise ValueError("The release source allowlist could not be loaded") from None
    findings = []
    for path, relative in files:
        findings.extend(inspect_bytes(relative.as_posix(), path.read_bytes(), denylist,
                                      strict_fixture_urls=strict_fixture_urls))
    return len(files), findings


def check_zip(path, denylist=None, *, strict_fixture_urls=False):
    findings = []
    try:
        with zipfile.ZipFile(path) as archive:
            files = [entry for entry in archive.infolist() if not entry.is_dir()]
            for entry in files:
                findings.extend(inspect_bytes(entry.filename, archive.read(entry), denylist,
                                              strict_fixture_urls=strict_fixture_urls))
    except (OSError, ValueError, zipfile.BadZipFile):
        raise ValueError("The release ZIP could not be inspected") from None
    return len(files), findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--denylist", type=Path, help="Ignored local JSON of protected values; never printed")
    parser.add_argument("--zip", type=Path, dest="archive")
    parser.add_argument("--strict-fixture-urls", action="store_true")
    args = parser.parse_args(argv)
    try:
        denylist = load_denylist(args.denylist)
        if args.archive:
            count, findings = check_zip(args.archive, denylist, strict_fixture_urls=args.strict_fixture_urls)
        else:
            count, findings = check_tree(args.root, denylist, strict_fixture_urls=args.strict_fixture_urls)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    errors = sum(item.severity == "error" for item in findings)
    print(json.dumps({"ok": errors == 0, "files": count, "errors": errors,
                      "warnings": len(findings) - errors,
                      "findings": [asdict(item) for item in findings]}, ensure_ascii=True, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
