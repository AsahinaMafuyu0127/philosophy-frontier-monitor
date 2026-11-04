"""Offline contracts for the standalone WorkBuddy evidence adapter."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "integrations/workbuddy/philosophy-frontier-monitor-workbuddy/scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper():
    return load_module("workbuddy_audit", SCRIPTS / "audit_result.py")


@pytest.fixture
def search():
    return {
        "ok": True,
        "operation": "search",
        "queried_at": "2026-01-01T00:00:00+00:00",
        "total_matches": 8,
        "stats": {},
        "items": [{"title": "Paper", "source_urls": ["https://philpapers.org/rec/PAPER"]}],
        "unconfirmed_records": [
            {
                "source_urls": ["https://philpapers.org/rec/WAIT"],
                "reason": "type_unverified",
                "stable_url": None,
            }
        ],
        "year_review_candidates": [
            {
                "review_record_url": "https://philpapers.org/rec/YEAR",
                "reason": "year_review_pending",
            }
        ],
        "source_failures": ["OpenAlex: lookup failed"],
        "wanfang_scan": {
            "status": "failed",
            "total_candidates": 9,
            "attempted": 1,
            "checks": [],
            "failure": "http_403",
        },
        "wanfang_discovery": {
            "status": "failed",
            "requested_pages": 3,
            "result_pages": 0,
            "failures": ["http_403"] * 3,
        },
        "official_issue_status": "local_only",
        "official_issue_leads": 3,
        "network_telemetry": {
            "sources": {"OpenAlex": {"failed_requests": 1, "deferred_long_retry_after": 1}}
        },
        "state_mutated": False,
        "state_advanced": False,
    }


def search_queue(items):
    return {
        "schema_version": 1,
        "mode": "search",
        "queried_at": "2026-01-01T08:00:00+08:00",
        "items": items,
    }


def pair(number=0, labels=None):
    return {
        "source": "Crossref",
        "record_id": f"10.1234/{number}",
        "title": "External paper",
        "authors": ["Example Author"],
        "doi": f"10.1234/{number}",
        "publication_date": "2025-01-01",
        "stable_url": f"https://doi.org/10.1234/{number}",
        "reason": "comparison needs review",
        "evidence_fingerprint": f"{number:064x}",
        "evidence": labels or ["orthographic_title", "author_conflict"],
    }


def test_original_membership_outranks_supporting_queue_and_preserves_absence(helper, search):
    queue = search_queue(
        [{"record_url": "https://philpapers.org/rec/PAPER", "reason": "confirmed_type"}]
    )
    result = helper.audit(search, queue, ["PAPER", "WAIT", "YEAR", "ABSENT"])
    assert [r["status"] for r in result["records"]] == [
        "confirmed_search_match",
        "unconfirmed_this_run",
        "year_review_pending",
        "not_located_in_returned_lists_gate_unknown",
    ]
    hit = result["records"][0]["hits"]["items"][0]
    assert hit["links"][0]["locator"] == "result.items[0].source_urls[0]"
    assert result["reported_counts"]["total_matches"]["value"] == 8
    assert result["computed_counts"]["result_lists"]["items"] == 1
    assert result["computed_counts"]["queue_reasons"] == {"confirmed_type": 1}
    assert result["url_field_presence"]["items"]["stable_url"] == {"absent": 1}
    assert result["url_field_presence"]["unconfirmed_records"]["stable_url"] == {"null": 1}
    assert result["state"]["state_advanced"]["value"] is False
    assert result["state"]["weekly_notifications_written"]["state"] == "absent"
    assert result["sources"]["explicit"]["wanfang_scan"]["attempted"]["value"] == 1
    assert result["sources"]["official_issue_leads"]["value"] == 3
    assert result["queue_binding"]["status"] == "query_time_matched"
    text = helper.markdown(result)
    assert "http_403" in text and "type_unverified" in text
    assert "未定位；具体关口未知" in text


def test_exact_pair_counts_and_record_links_for_large_queue(helper, search):
    items = []
    number = 0
    for index in range(23):
        records = []
        for _ in range(58 if index == 0 else (15 if index == 1 else 2)):
            if number == 115:
                break
            labels = (
                ["orthographic_title", "author_conflict"]
                if number < 97
                else ["compatible_first_author", "partial_title_similarity"]
            )
            records.append(pair(number, labels))
            number += 1
        items.append(
            {
                "record_url": f"https://philpapers.org/rec/EX{index}",
                "reason": "identity_unverified",
                "identity_review_pairs": records,
            }
        )
    result = helper.audit(search, search_queue(items), [])
    counts = result["computed_counts"]
    assert counts["identity_records"] == 23
    assert counts["identity_pairs"] == 115
    assert counts["identity_evidence"] == [
        {"labels": ["author_conflict", "orthographic_title"], "count": 97},
        {"labels": ["compatible_first_author", "partial_title_similarity"], "count": 18},
    ]
    assert counts["pairs_missing_record_link"] == 0
    assert result["identity_pairs"][0]["record_links"][0]["locator"] == "queue.items[0].record_url"


@pytest.mark.parametrize("when", ["2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00"])
def test_rejects_mixed_search_runs_or_naive_timestamps(helper, search, when):
    queue = search_queue([])
    queue["queried_at"] = when
    with pytest.raises(ValueError):
        helper.audit(search, queue, [])


@pytest.mark.parametrize("mode", ["weekly-run", "pull-now"])
def test_monitoring_sources_split_sections_and_missing_state(helper, mode):
    result = {
        "ok": True,
        "operation": mode,
        "window_start": "2026-01-01 00:00:00+00:00",
        "window_end": "2026-01-08 00:00:00+00:00",
        "dry_run": True,
        "stats": {"matched": 2, "confirmed_source_arrivals": 2, "automatic_retry_required": 1},
        "report_markdown": "\n".join(
            [
                "### Recently arrived (1)",
                "### Recently changed (1)",
                "- `openalex-bibliography`：failed；HTTP 400; non_retryable_status",
                "- `official-journals`：local_only；local records",
                "- `wanfang-discovery`: failed; three scopes failed",
                "- `openalex-bibliography`: failed; repeated English coverage",
            ]
        ),
    }
    queue = {
        "schema_version": 1,
        "window_start": "2026-01-01T08:00:00+08:00",
        "window_end": "2026-01-08T08:00:00+08:00",
        "items": [],
    }
    out = helper.audit(result, queue, ["PAPER"])
    assert out["queue_binding"]["status"] == "window_matched"
    assert out["queue_binding"]["same_run_proven"] is False
    assert out["reported_counts"]["confirmed_source_arrivals"]["value"] == 2
    assert out["display_section_counts"]["recently_arrived"]["value"] == 1
    assert out["display_section_counts"]["recently_changed"]["value"] == 1
    assert len(out["sources"]["report_coverage"]) == 3
    assert out["state"]["state_advanced"]["state"] == "absent"
    assert out["records"][0]["status"] == "final_status_not_exposed_by_result_lists"
    queue["window_end"] = "2026-01-09T00:00:00+00:00"
    with pytest.raises(ValueError, match="different windows"):
        helper.audit(result, queue, [])


def test_pair_overlap_is_fingerprint_bound_and_does_not_claim_application(helper, search):
    comparison = pair(7)
    queue = search_queue(
        [{"record_url": "https://philpapers.org/rec/PAPER", "identity_review_pairs": [comparison]}]
    )
    decision = {
        "record_url": "https://philpapers.org/rec/PAPER",
        "source": "Crossref",
        "external_record_id": comparison["record_id"],
        "evidence_fingerprint": comparison["evidence_fingerprint"],
    }
    reviews = {"schema_version": 1, "reviews": [decision]}
    out = helper.audit(search, queue, [], reviews)
    assert out["computed_counts"]["pending_pairs_matching_supplied_decisions"] == 1
    decision["evidence_fingerprint"] = "f" * 64
    assert (
        helper.audit(search, queue, [], reviews)["computed_counts"][
            "pending_pairs_matching_supplied_decisions"
        ]
        == 0
    )


def test_no_queue_is_unexamined_not_empty(helper, search):
    result = helper.audit(search, None, [])
    assert result["queue_binding"]["status"] == "not_supplied"
    assert result["computed_counts"]["identity_pairs"] is None
    assert result["computed_counts"]["pairs_missing_record_link"] is None


def run_script(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "audit_result.py"), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_cli_writes_parseable_private_artifacts_and_preserves_existing_files(tmp_path, search):
    source, output, report = (tmp_path / name for name in ("input.json", "audit.json", "audit.md"))
    source.write_text(json.dumps(search), encoding="utf-8")
    run = run_script(
        "--result", source, "--record", "PAPER", "--output", output, "--markdown", report
    )
    assert run.returncode == 0, run.stderr
    assert (
        json.loads(output.read_text(encoding="utf-8"))["records"][0]["status"]
        == "confirmed_search_match"
    )
    assert "已确认检索匹配" in report.read_text(encoding="utf-8")
    before = source.read_bytes(), output.read_bytes(), report.read_bytes()
    again = run_script("--result", source, "--output", output, "--markdown", report)
    assert again.returncode == 2
    assert before == (source.read_bytes(), output.read_bytes(), report.read_bytes())
    assert run_script("--result", source, "--output", source).returncode == 2


def test_cli_rejects_console_transcript_instead_of_silently_rewriting_it(tmp_path, search):
    source, output = tmp_path / "console.txt", tmp_path / "audit.json"
    original = "progress\n" + json.dumps(search) + "\nEXIT_CODE:0"
    source.write_text(original, encoding="utf-8")
    run = run_script("--result", source, "--output", output)
    assert run.returncode == 2
    assert not output.exists()
    assert source.read_text(encoding="utf-8") == original


def test_builder_public_allowlist_hashes_and_reproducibility(tmp_path):
    builder = load_module("workbuddy_builder", ROOT / "scripts/build_workbuddy_skill.py")
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname="philosophy-frontier-monitor"\n', encoding="utf-8"
    )
    (source / "SKILL.md").write_text("public skill", encoding="utf-8")
    (source / "references").mkdir()
    (source / "references/public.md").write_text("public policy", encoding="utf-8")
    (source / "references/untracked-private.md").write_text("private", encoding="utf-8")
    (source / "config").mkdir()
    (source / "config/official-journals.yaml").write_text("journals: []", encoding="utf-8")
    (source / "config/watchlist.yaml").write_text("private", encoding="utf-8")
    for args in (
        ("init",),
        ("add", "SKILL.md", "references/public.md", "config/official-journals.yaml"),
        (
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ),
    ):
        subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    builder.build(source, first)
    builder.build(source, second)
    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        assert not any("private" in name or "watchlist" in name for name in archive.namelist())
        prefix = "philosophy-frontier-monitor-workbuddy/"
        manifest = json.loads(archive.read(prefix + "references/package-manifest.json"))
        assert manifest["package_version"] == "0.2.9"
        for member, expected in {**manifest["snapshot_files"], **manifest["package_files"]}.items():
            assert builder.digest(archive.read(member)) == expected
