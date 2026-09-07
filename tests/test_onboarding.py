import json
from pathlib import Path

import yaml

from philosophy_frontier_monitor.cli import main

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PROJECT_ROOT = FIXTURE_DIR.parents[1]


def _payload(capsys):
    return json.loads(capsys.readouterr().out)


def test_onboarding_status_requests_both_inputs_when_private_profile_is_absent(
    workspace_tmp_path, capsys
):
    result = main(["onboarding-status", "--config", str(workspace_tmp_path / "missing.yaml")])

    assert result == 0
    payload = _payload(capsys)
    assert payload["profile_exists"] is False
    assert payload["needs_research_direction"] is True
    assert payload["needs_philpapers_account_decision"] is True


def test_onboarding_status_only_requests_account_choice_for_legacy_profile(capsys):
    result = main(["onboarding-status", "--config", str(FIXTURE_DIR / "watchlist_minimal.yaml")])

    assert result == 0
    payload = _payload(capsys)
    assert payload["profile_exists"] is True
    assert payload["needs_research_direction"] is False
    assert payload["needs_philpapers_account_decision"] is True
    assert payload["onboarding_complete"] is False
    assert "original_text" not in payload


def test_onboarding_status_does_not_repeat_completed_first_dialogue(capsys):
    result = main(
        [
            "onboarding-status",
            "--config",
            str(PROJECT_ROOT / "config" / "watchlist.example.yaml"),
        ]
    )

    assert result == 0
    payload = _payload(capsys)
    assert payload["profile_exists"] is True
    assert payload["needs_research_direction"] is False
    assert payload["needs_philpapers_account_decision"] is False
    assert payload["onboarding_complete"] is True
    assert payload["public_profile_url_recorded"] is False


def test_onboarding_status_never_prints_authorized_profile_url(workspace_tmp_path, capsys):
    source = PROJECT_ROOT / "config" / "watchlist.example.yaml"
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    account = payload["onboarding"]["philpapers_account"]
    account["decision"] = "authorized_public_only"
    account["public_profile_url"] = "https://philpeople.org/profiles/example-researcher"
    account["authorized_scopes"] = ["self_declared_interests"]
    target = workspace_tmp_path / "watchlist.yaml"
    target.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    result = main(["onboarding-status", "--config", str(target)])

    assert result == 0
    raw_output = capsys.readouterr().out
    status = json.loads(raw_output)
    assert status["public_profile_url_recorded"] is True
    assert "example-researcher" not in raw_output
