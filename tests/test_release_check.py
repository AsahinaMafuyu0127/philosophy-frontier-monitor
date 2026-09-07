import shutil
import uuid
from pathlib import Path

import pytest

from philosophy_frontier_monitor.release_check import (
    REQUIRED_IGNORE_RULES,
    REQUIRED_PUBLIC_FILES,
    audit_release,
)


def make_project(root: Path) -> Path:
    for relative in REQUIRED_PUBLIC_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == ".gitignore":
            path.write_text("\n".join(REQUIRED_IGNORE_RULES) + "\n", encoding="utf-8")
        elif relative == "config/watchlist.example.yaml":
            path.write_text(
                "interest:\n"
                '  profile_id: "pfm:interest:example"\n'
                '  original_text: "虚构公开示例"\n',
                encoding="utf-8",
            )
        elif relative == "SECURITY.md":
            path.write_text("Use private security reporting.\n", encoding="utf-8")
        else:
            path.write_text(f"public fixture: {relative}\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def check(audit, code: str):
    return next(item for item in audit.checks if item.code == code)


@pytest.fixture
def release_root():
    path = Path(__file__).parents[1] / "var" / "test-release-check" / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_release_check_separates_technical_blockers_from_maintainer_decisions(release_root):
    root = make_project(release_root)

    audit = audit_release(root)

    assert audit.release_ready is False
    assert "git_repository" in audit.blocker_codes
    assert "third_party_notices" in audit.blocker_codes
    assert "project_license" in audit.decision_codes
    assert check(audit, "private_path_ignore_rules").status == "pass"
    assert check(audit, "untracked_public_files").status == "not_applicable"
    assert check(audit, "high_confidence_secret_scan").status == "pass"


def test_release_check_reports_secret_detector_without_echoing_value(release_root):
    root = make_project(release_root)
    secret = "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
    (root / "src" / "leak.py").write_text(f'TOKEN = "{secret}"\n', encoding="utf-8")

    audit = audit_release(root)
    item = check(audit, "high_confidence_secret_scan")
    serialized = str(audit.to_dict())

    assert item.status == "blocker"
    assert item.details == ("src/leak.py:github_token",)
    assert secret not in serialized


def test_release_check_detects_exact_private_research_text_duplication(release_root):
    root = make_project(release_root)
    private = root / "config" / "watchlist.yaml"
    private.write_text(
        'interest:\n  profile_id: "pfm:interest:private"\n  original_text: "不公开的研究问题"\n',
        encoding="utf-8",
    )
    example = root / "config" / "watchlist.example.yaml"
    example.write_text(
        'interest:\n  profile_id: "pfm:interest:example"\n  original_text: "不公开的研究问题"\n',
        encoding="utf-8",
    )

    audit = audit_release(root)
    item = check(audit, "private_profile_separation")

    assert item.status == "blocker"
    assert item.details == ("interest.original_text",)
    assert "不公开的研究问题" not in str(audit.to_dict())


def test_release_check_blocks_complete_philpapers_taxonomy_outside_private_cache(release_root):
    root = make_project(release_root)
    taxonomy = root / "tests" / "fixtures" / "taxonomy.json"
    taxonomy.parent.mkdir(parents=True)
    taxonomy.write_text(
        '{"source":"philpapers","complete":true,"fixture":false,"categories":[]}',
        encoding="utf-8",
    )

    audit = audit_release(root)
    item = check(audit, "production_taxonomy_redistribution")

    assert item.status == "blocker"
    assert item.details == ("tests/fixtures/taxonomy.json",)
