"""Copy release-allowlisted source into a new installation directory."""
import argparse
import importlib.util
from pathlib import Path
import shutil


def copy_source(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents:
        raise ValueError("Install destination must be outside the downloaded source tree")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Install destination must be empty")
    spec = importlib.util.spec_from_file_location("creator_notes_release", Path(__file__).with_name("build_release.py"))
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    files = list(release.selected_files(source))
    for path, _ in files:
        release.scan_text(path)
    for path, relative in files:
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, output)
    return len(files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print("Copied", copy_source(args.source, args.destination), "source files")
