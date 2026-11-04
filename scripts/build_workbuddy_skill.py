"""Build the WorkBuddy adapter and an allowlisted public project snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
NAME = "philosophy-frontier-monitor-workbuddy"
ADAPTER = ROOT / "integrations" / "workbuddy" / NAME
ADAPTER_FILES = (
    "SKILL.md",
    "references/preflight.md",
    "references/search-result-evidence.md",
    "references/audit-output.md",
    "scripts/preflight.py",
    "scripts/audit_search_records.py",
    "scripts/audit_result.py",
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={root.as_posix()}", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        stderr=subprocess.PIPE,
    ).strip()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(source: Path, output: Path, stage: Path | None = None) -> dict:
    source = source.resolve()
    output = output.resolve()
    if output.exists() or (stage is not None and stage.exists()):
        raise ValueError("choose new archive/stage paths; existing outputs are preserved")
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    if project.get("project", {}).get("name") != "philosophy-frontier-monitor":
        raise ValueError("selected source is not the monitor project")
    wrapper = (ADAPTER / "SKILL.md").read_text(encoding="utf-8")
    metadata = dict(
        line.split(": ", 1) for line in wrapper.split("---", 2)[1].splitlines() if ": " in line
    )
    if metadata.get("name") != NAME or not metadata.get("version"):
        raise ValueError("invalid WorkBuddy frontmatter")
    public = git(
        source, "ls-files", "--", "SKILL.md", "references/*.md", "config/official-journals.yaml"
    ).splitlines()
    if "SKILL.md" not in public or "config/official-journals.yaml" not in public:
        raise ValueError("source public files are not tracked")
    files = {}
    snapshot_hashes = {}
    package_hashes = {}
    for relative in public:
        path = (source / relative).resolve()
        if not path.is_relative_to(source) or not path.is_file():
            raise ValueError(f"invalid public snapshot file: {relative}")
        member = f"{NAME}/references/project/{relative}"
        files[member] = path.read_bytes()
        snapshot_hashes[member] = digest(files[member])
    for relative in ADAPTER_FILES:
        member = f"{NAME}/{relative}"
        files[member] = (ADAPTER / relative).read_bytes()
        package_hashes[member] = digest(files[member])
    manifest = {
        "format_version": 1,
        "package_version": metadata["version"],
        "source_commit": git(source, "rev-parse", "HEAD"),
        "source_project_root_hint": str(source),
        "source_skill_sha256": digest((source / "SKILL.md").read_bytes()),
        "snapshot_files": snapshot_hashes,
        "package_files": package_hashes,
    }
    files[f"{NAME}/references/package-manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x") as archive:
        for member, data in sorted(files.items()):
            info = ZipInfo(member, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    with ZipFile(output) as archive:
        if archive.testzip() is not None or archive.namelist() != sorted(files):
            raise ValueError("archive validation failed")
        if any(archive.read(member) != data for member, data in files.items()):
            raise ValueError("archive does not match source files")
    if stage:
        for member, data in files.items():
            path = stage / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return {
        "archive": str(output),
        "package_version": metadata["version"],
        "source_commit": manifest["source_commit"],
        "files": len(files),
        "sha256": digest(output.read_bytes()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source_root, args.output, args.stage), ensure_ascii=False))


if __name__ == "__main__":
    main()
