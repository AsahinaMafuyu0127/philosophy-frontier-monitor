from pathlib import Path

import pytest
import yaml

from philosophy_frontier_monitor.config import ConfigError, load_watchlist

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_loads_confirmed_category_ids_without_remapping_text():
    config = load_watchlist(FIXTURE_DIR / "watchlist_minimal.yaml")

    assert config.profile_id == "pfm:interest:test"
    assert config.profile_version == 3
    assert [item.category_id for item in config.confirmed_categories] == ["74924"]
    assert config.feeds[0].feed_key == "philpapers-rss:74924"
    assert config.taxonomy_path == FIXTURE_DIR / "philpapers_categories_small.json"
    assert config.max_unresolved_attempts == 4
    assert config.inference_mode == "adaptive"
    assert config.proposed_categories == ()
    assert config.schedule.local_time.isoformat() == "08:00:00"
    assert config.schedule.catch_up_missed_windows
    assert config.schedule.catch_up_after_login
    assert config.schedule.failure_notification == "codex"
    assert config.onboarding.philpapers_account_decision == "not_recorded"
    assert config.onboarding.complete is False


def test_rejects_old_config_schema():
    with pytest.raises(ConfigError, match="schema_version"):
        load_watchlist(FIXTURE_DIR / "watchlist_old_schema.yaml")


def test_parses_pending_proposals_but_keeps_them_out_of_confirmed_categories():
    config = load_watchlist(FIXTURE_DIR / "watchlist_with_proposal.yaml")

    assert [item.category_id for item in config.confirmed_categories] == ["74924"]
    assert [item.category_id for item in config.proposed_categories] == ["74844"]
    assert config.inference_mode == "exploratory"
    assert config.report_weekday == 6
    assert config.schedule.local_time.isoformat() == "20:30:00"
    assert config.profile_effective_from is not None
    assert config.profile_effective_from.isoformat() == "2026-09-05T12:30:00+08:00"


def test_rejects_proposal_that_is_already_confirmed():
    with pytest.raises(ConfigError, match="already confirmed"):
        load_watchlist(FIXTURE_DIR / "watchlist_proposal_already_confirmed.yaml")


def test_public_schema_five_example_records_onboarding_without_account_access():
    config = load_watchlist(FIXTURE_DIR.parents[1] / "config" / "watchlist.example.yaml")

    assert config.schedule.catch_up_missed_windows is True
    assert config.original_text.startswith("虚构示例：")
    assert config.onboarding.complete is True
    assert config.onboarding.philpapers_account_decision == "deferred"
    assert config.onboarding.authorized_scopes == ()
    assert config.onboarding.public_profile_url is None


def _write_schema_five(workspace_tmp_path, mutate):
    source = FIXTURE_DIR.parents[1] / "config" / "watchlist.example.yaml"
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    mutate(payload["onboarding"]["philpapers_account"])
    target = workspace_tmp_path / "watchlist.yaml"
    target.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return target


def test_schema_five_accepts_only_explicit_public_account_scopes(workspace_tmp_path):
    def authorize(account):
        account["decision"] = "authorized_public_only"
        account["public_profile_url"] = "https://philpeople.org/profiles/example-researcher"
        account["authorized_scopes"] = ["self_declared_interests", "my_works"]

    config = load_watchlist(_write_schema_five(workspace_tmp_path, authorize))

    assert config.onboarding.complete is True
    assert config.onboarding.authorized_scopes == ("self_declared_interests", "my_works")


@pytest.mark.parametrize(
    "profile_url",
    [
        "http://philpapers.org/profile/example",
        "https://example.test/profile/example",
        "https://user:secret@philpapers.org/profile/example",
        "https://philpapers.org:8443/profile/example",
        "https://philpapers.org/profile/example?apiKey=secret",
    ],
)
def test_schema_five_rejects_unsafe_profile_urls(workspace_tmp_path, profile_url):
    def authorize(account):
        account["decision"] = "authorized_public_only"
        account["public_profile_url"] = profile_url
        account["authorized_scopes"] = ["self_declared_interests"]

    with pytest.raises(ConfigError, match="profile URL"):
        load_watchlist(_write_schema_five(workspace_tmp_path, authorize))


def test_declined_account_access_cannot_retain_url_or_scopes(workspace_tmp_path):
    def retain_without_consent(account):
        account["decision"] = "declined"
        account["public_profile_url"] = "https://philpeople.org/profiles/example-researcher"
        account["authorized_scopes"] = ["self_declared_interests"]

    with pytest.raises(ConfigError, match="must not retain"):
        load_watchlist(_write_schema_five(workspace_tmp_path, retain_without_consent))
