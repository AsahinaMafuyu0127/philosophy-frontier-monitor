"""Build the allowlisted, local-only WorkBuddy Skill ZIP."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "philosophy-frontier-monitor-workbuddy"
SOURCE = ROOT / "integrations" / "workbuddy" / SKILL_NAME / "SKILL.md"
DEFAULT_OUTPUT = ROOT / "var" / "workbuddy-pilot" / f"{SKILL_NAME}.zip"
REQUIRED_KEYS = (
    "name", "description", "description_zh", "description_en", "version", "author"
)


def build(output: Path) -> None:
    source_bytes = SOURCE.read_bytes()
    text = source_bytes.decode("utf-8-sig").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("WorkBuddy Skill needs YAML frontmatter")
    frontmatter = text.split("---\n", 2)[1]
    for key in REQUIRED_KEYS:
        if not any(
            line.startswith(f"{key}: ") and line[len(key) + 2 :].strip()
            for line in frontmatter.splitlines()
        ):
            raise ValueError(f"missing WorkBuddy metadata: {key}")
    if f"name: {SKILL_NAME}" not in frontmatter.splitlines():
        raise ValueError("Skill name must match its ZIP root")
    member = f"{SKILL_NAME}/SKILL.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    info = ZipInfo(member, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    with ZipFile(output, "w") as archive:
        archive.writestr(info, source_bytes)
    with ZipFile(output) as archive:
        if archive.namelist() != [member] or archive.read(member) != source_bytes:
            raise ValueError("WorkBuddy package did not match the public allowlist")
    print(f"Built {output} ({member} only)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == "__main__":
    main()
