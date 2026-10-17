"""Build a public, versioned snapshot of the local WorkBuddy Skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "philosophy-frontier-monitor-workbuddy"
WRAPPER = ROOT / "integrations" / "workbuddy" / SKILL_NAME / "SKILL.md"
OUTPUT_DIR = ROOT / "var" / "workbuddy-pilot"
POLICY_FILES = (
    "references/chinese-source-expansion.md",
    "references/date-and-version-semantics.md",
    "references/first-dialogue-onboarding.md",
    "references/installation.md",
    "references/interest-inference-policy.md",
    "references/interest-tag-matching.md",
    "references/metadata-model.md",
    "references/monitoring-policy.md",
    "references/paper-search.md",
    "references/release-readiness.md",
    "references/resource-and-scope-warnings.md",
    "references/security-and-privacy.md",
    "references/source-catalog.md",
    "references/source-retry-policy.md",
    "references/taxonomy-lifecycle.md",
    "references/work-type-policy.md",
)
CATALOG = "config/official-journals.yaml"
REQUIRED_KEYS = (
    "name",
    "description",
    "description_zh",
    "description_en",
    "version",
    "author",
)
ZIP_PREFIX = f"{SKILL_NAME}/"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_wrapper(data: bytes) -> None:
    text = data.decode("utf-8-sig").replace("\r\n", "\n")
    if not text.startswith("---\n") or "---\n" not in text[4:]:
        raise ValueError("WorkBuddy Skill needs YAML frontmatter")
    frontmatter = text.split("---\n", 2)[1]
    values = {}
    for line in frontmatter.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            values[key] = value.strip()
    for key in REQUIRED_KEYS:
        if not values.get(key):
            raise ValueError(f"missing WorkBuddy metadata: {key}")
    if values["name"] != SKILL_NAME:
        raise ValueError("Skill name must match its ZIP root")


def _read_public_file(source_root: Path, relative: str) -> bytes:
    path = (source_root / relative).resolve()
    if not path.is_relative_to(source_root):
        raise ValueError(f"public file escapes the selected project: {relative}")
    return path.read_bytes()


def build(source_root: Path, output: Path | None = None) -> Path:
    source_root = source_root.resolve()
    project = tomllib.loads(_read_public_file(source_root, "pyproject.toml").decode("utf-8"))
    if project.get("project", {}).get("name") != "philosophy-frontier-monitor":
        raise ValueError("selected source is not the monitor project")

    wrapper = WRAPPER.read_bytes()
    _validate_wrapper(wrapper)
    project_skill = _read_public_file(source_root, "SKILL.md")
    skill_text = project_skill.decode("utf-8-sig")
    referenced = set(re.findall(r"references/[A-Za-z0-9_-]+\.md", skill_text))
    unlisted = referenced.difference(POLICY_FILES)
    if unlisted:
        raise ValueError(
            f"new policy references need a public allowlist review: {sorted(unlisted)}"
        )

    files = {ZIP_PREFIX + "SKILL.md": wrapper}
    snapshot = ("SKILL.md", *POLICY_FILES, CATALOG)
    for relative in snapshot:
        member = ZIP_PREFIX + "references/project/" + relative
        files[member] = _read_public_file(source_root, relative)
    skill_hash = _sha256(project_skill)
    manifest = {
        "format_version": 1,
        "source_skill_sha256": skill_hash,
        "snapshot_files": {
            member: _sha256(data)
            for member, data in sorted(files.items())
            if member != ZIP_PREFIX + "SKILL.md"
        },
    }
    files[ZIP_PREFIX + "references/package-manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if output is None:
        output = OUTPUT_DIR / f"{SKILL_NAME}-{skill_hash[:12]}.zip"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w") as archive:
        for member, data in sorted(files.items()):
            info = ZipInfo(member, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    with ZipFile(output) as archive:
        if archive.namelist() != sorted(files):
            raise ValueError("WorkBuddy package has unexpected files")
        for member, data in files.items():
            if archive.read(member) != data:
                raise ValueError(f"WorkBuddy package differs from source: {member}")
    print(f"Built {output} ({len(files)} public files; Skill SHA-256 {skill_hash})")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    build(args.source_root, args.output)


if __name__ == "__main__":
    main()
