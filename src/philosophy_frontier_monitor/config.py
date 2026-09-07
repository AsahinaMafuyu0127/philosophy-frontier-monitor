"""Validated watchlist configuration for baseline and weekly runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConfirmedCategoryConfig:
    category_id: str
    category_name: str
    include_descendants: bool


@dataclass(frozen=True, slots=True)
class ProposedCategoryConfig:
    category_id: str
    category_name: str
    relation: str
    rationale: str
    evidence_sources: tuple[str, ...]
    breadth_note: str
    status: str


@dataclass(frozen=True, slots=True)
class ScheduleConfig:
    local_time: time
    catch_up_missed_windows: bool
    defer_for_explicit_high_priority_work: bool
    failure_notification: str

    @property
    def catch_up_after_login(self) -> bool:
        """Compatibility alias for configuration schema 0.2 and 0.3."""

        return self.catch_up_missed_windows


@dataclass(frozen=True, slots=True)
class FeedConfig:
    category_id: str
    category_name: str
    url: str

    @property
    def feed_key(self) -> str:
        return f"philpapers-rss:{self.category_id}"


@dataclass(frozen=True, slots=True)
class PhilArchiveOAIConfig:
    enabled: bool
    endpoint: str


@dataclass(frozen=True, slots=True)
class StorageConfig:
    state_database: Path
    report_directory: Path


@dataclass(frozen=True, slots=True)
class OnboardingConfig:
    completed_at: datetime | None
    philpapers_account_decision: str
    decision_recorded_at: datetime | None
    public_profile_url: str | None
    authorized_scopes: tuple[str, ...]

    @property
    def account_decision_recorded(self) -> bool:
        return self.philpapers_account_decision != "not_recorded"

    @property
    def complete(self) -> bool:
        return self.completed_at is not None and self.account_decision_recorded


@dataclass(frozen=True, slots=True)
class WatchlistConfig:
    path: Path
    timezone: ZoneInfo
    report_weekday: int
    profile_id: str
    profile_version: int
    profile_effective_from: datetime | None
    original_text: str
    confirmed_categories: tuple[ConfirmedCategoryConfig, ...]
    proposed_categories: tuple[ProposedCategoryConfig, ...]
    inference_mode: str
    excluded_category_ids: frozenset[str]
    onboarding: OnboardingConfig
    schedule: ScheduleConfig
    taxonomy_path: Path
    allow_fixture_for_dry_run: bool
    feeds: tuple[FeedConfig, ...]
    philarchive_oai: PhilArchiveOAIConfig
    crossref_mailto: str | None
    openalex_mailto: str | None
    max_unresolved_attempts: int
    unresolved_retry_days: int
    storage: StorageConfig


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

INFERENCE_MODES = {"minimal", "adaptive", "exploratory"}
INFERENCE_RELATIONS = {
    "directly_implied",
    "primary_text",
    "conceptual_adjacency",
    "historical_reception",
    "contemporary_bridge",
    "methodological_context",
}
PROPOSAL_STATUSES = {"pending", "deferred", "rejected"}
PHILPAPERS_ACCOUNT_DECISIONS = {"declined", "deferred", "authorized_public_only"}
PHILPAPERS_ACCOUNT_SCOPES = {
    "self_declared_interests",
    "public_bibliographies",
    "public_reading_lists",
    "my_works",
}
PHILOSOPHY_PROFILE_HOSTS = {
    "philpapers.org",
    "www.philpapers.org",
    "philpeople.org",
    "www.philpeople.org",
}


def _require_mapping(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _require_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be non-empty text")
    return value.strip()


def _resolve_project_path(value: object, *, project_root: Path, field: str) -> Path:
    text = _require_text(value, field)
    path = Path(text)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _optional_mailto(value: object, path: str) -> str | None:
    if value is None:
        return None
    text = _require_text(value, path)
    if "@" not in text or any(character.isspace() for character in text):
        raise ConfigError(f"{path} must be a valid-looking email address")
    return text


def _philarchive_oai_endpoint(value: object, path: str) -> str:
    text = _require_text(value, path)
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise ConfigError(f"{path} is not a valid URL") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"philarchive.org", "www.philarchive.org"}
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/oai.pl"
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigError(f"{path} must be the credential-free HTTPS PhilArchive OAI endpoint")
    return text


def _parse_local_time(value: object, path: str) -> time:
    text = _require_text(value, path)
    try:
        parsed = time.fromisoformat(text)
    except ValueError as error:
        raise ConfigError(f"{path} must use HH:MM or HH:MM:SS") from error
    if parsed.tzinfo is not None:
        raise ConfigError(f"{path} must be a local wall-clock time without an offset")
    return parsed


def _parse_optional_instant(value: object, path: str) -> datetime | None:
    if value is None:
        return None
    text = _require_text(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ConfigError(f"{path} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ConfigError(f"{path} must include a timezone offset")
    return parsed


def _public_profile_url(value: object, path: str) -> str:
    text = _require_text(value, path)
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise ConfigError(f"{path} is not a valid profile URL") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in PHILOSOPHY_PROFILE_HOSTS
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path in {"", "/"}
    ):
        raise ConfigError(
            f"{path} must be a credential-free HTTPS PhilPapers or PhilPeople profile URL"
        )
    return text


def _parse_onboarding(root: dict, schema_version: str) -> OnboardingConfig:
    if schema_version != "0.5":
        return OnboardingConfig(
            completed_at=None,
            philpapers_account_decision="not_recorded",
            decision_recorded_at=None,
            public_profile_url=None,
            authorized_scopes=(),
        )

    onboarding = _require_mapping(root.get("onboarding"), "onboarding")
    completed_at = _parse_optional_instant(
        onboarding.get("completed_at"), "onboarding.completed_at"
    )
    if completed_at is None:
        raise ConfigError("onboarding.completed_at is required in schema 0.5")
    account = _require_mapping(
        onboarding.get("philpapers_account"), "onboarding.philpapers_account"
    )
    decision = _require_text(
        account.get("decision"), "onboarding.philpapers_account.decision"
    ).casefold()
    if decision not in PHILPAPERS_ACCOUNT_DECISIONS:
        raise ConfigError(f"unsupported PhilPapers account decision: {decision}")
    decision_recorded_at = _parse_optional_instant(
        account.get("decision_recorded_at"),
        "onboarding.philpapers_account.decision_recorded_at",
    )
    if decision_recorded_at is None:
        raise ConfigError(
            "onboarding.philpapers_account.decision_recorded_at is required in schema 0.5"
        )
    scopes_raw = account.get("authorized_scopes", [])
    if not isinstance(scopes_raw, list) or not all(
        isinstance(scope, str) and scope.strip() for scope in scopes_raw
    ):
        raise ConfigError(
            "onboarding.philpapers_account.authorized_scopes must be a list of scope names"
        )
    scopes = tuple(dict.fromkeys(scope.strip().casefold() for scope in scopes_raw))
    unknown_scopes = set(scopes).difference(PHILPAPERS_ACCOUNT_SCOPES)
    if unknown_scopes:
        raise ConfigError(f"unsupported PhilPapers account scopes: {sorted(unknown_scopes)}")

    raw_url = account.get("public_profile_url")
    if decision == "authorized_public_only":
        public_profile_url = _public_profile_url(
            raw_url, "onboarding.philpapers_account.public_profile_url"
        )
        if not scopes:
            raise ConfigError(
                "authorized PhilPapers account access requires at least one explicit public scope"
            )
    else:
        if raw_url is not None or scopes:
            raise ConfigError(
                "declined or deferred PhilPapers access must not retain a profile URL or scopes"
            )
        public_profile_url = None

    return OnboardingConfig(
        completed_at=completed_at,
        philpapers_account_decision=decision,
        decision_recorded_at=decision_recorded_at,
        public_profile_url=public_profile_url,
        authorized_scopes=scopes,
    )


def load_watchlist(path: str | Path) -> WatchlistConfig:
    config_path = Path(path).resolve()
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read config: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML: {error}") from error
    root = _require_mapping(payload, "config")
    schema_version = str(root.get("schema_version"))
    if schema_version not in {"0.2", "0.3", "0.4", "0.5"}:
        raise ConfigError("config.schema_version must be '0.2', '0.3', '0.4', or '0.5'")

    try:
        timezone = ZoneInfo(_require_text(root.get("timezone"), "timezone"))
    except ZoneInfoNotFoundError as error:
        raise ConfigError(f"unknown timezone: {root.get('timezone')}") from error
    weekday_name = _require_text(root.get("report_weekday"), "report_weekday").casefold()
    if weekday_name not in WEEKDAYS:
        raise ConfigError(f"unsupported report_weekday: {weekday_name}")

    # Repository examples use project-root-relative paths. A private config in
    # config/ follows the same rule, so it can be copied without rewriting paths.
    project_root = (
        config_path.parent.parent if config_path.parent.name == "config" else config_path.parent
    )

    interest = _require_mapping(root.get("interest"), "interest")
    confirmed_payload = interest.get("confirmed_categories")
    if not isinstance(confirmed_payload, list) or not confirmed_payload:
        raise ConfigError("interest.confirmed_categories must be a non-empty list")
    confirmed = []
    confirmed_ids: set[str] = set()
    for index, raw_item in enumerate(confirmed_payload):
        item = _require_mapping(raw_item, f"interest.confirmed_categories[{index}]")
        category_id = _require_text(
            item.get("category_id"), f"confirmed_categories[{index}].category_id"
        )
        if category_id in confirmed_ids:
            raise ConfigError(f"duplicate confirmed category_id: {category_id}")
        confirmed_ids.add(category_id)
        confirmed.append(
            ConfirmedCategoryConfig(
                category_id=category_id,
                category_name=_require_text(
                    item.get("category_name"), f"confirmed_categories[{index}].category_name"
                ),
                include_descendants=bool(item.get("include_descendants", False)),
            )
        )

    inference = _require_mapping(interest.get("inference", {}), "interest.inference")
    inference_mode = str(inference.get("mode", "adaptive")).casefold()
    if inference_mode not in INFERENCE_MODES:
        raise ConfigError(f"unsupported interest.inference.mode: {inference_mode}")

    proposed_payload = interest.get("proposed_categories", [])
    if not isinstance(proposed_payload, list):
        raise ConfigError("interest.proposed_categories must be a list")
    proposed = []
    proposed_ids: set[str] = set()
    for index, raw_item in enumerate(proposed_payload):
        item = _require_mapping(raw_item, f"interest.proposed_categories[{index}]")
        category_id = _require_text(
            item.get("category_id"), f"proposed_categories[{index}].category_id"
        )
        if category_id in confirmed_ids:
            raise ConfigError(f"proposed category is already confirmed: {category_id}")
        if category_id in proposed_ids:
            raise ConfigError(f"duplicate proposed category_id: {category_id}")
        proposed_ids.add(category_id)
        relation = _require_text(
            item.get("relation"), f"proposed_categories[{index}].relation"
        ).casefold()
        if relation not in INFERENCE_RELATIONS:
            raise ConfigError(f"unsupported proposed category relation: {relation}")
        status = _require_text(
            item.get("status", "pending"), f"proposed_categories[{index}].status"
        ).casefold()
        if status not in PROPOSAL_STATUSES:
            raise ConfigError(f"unsupported proposed category status: {status}")
        evidence_sources = item.get("evidence_sources", [])
        if (
            not isinstance(evidence_sources, list)
            or not evidence_sources
            or not all(isinstance(source, str) and source.strip() for source in evidence_sources)
        ):
            raise ConfigError(
                f"proposed_categories[{index}].evidence_sources must be a list of text"
            )
        proposed.append(
            ProposedCategoryConfig(
                category_id=category_id,
                category_name=_require_text(
                    item.get("category_name"),
                    f"proposed_categories[{index}].category_name",
                ),
                relation=relation,
                rationale=_require_text(
                    item.get("rationale"), f"proposed_categories[{index}].rationale"
                ),
                evidence_sources=tuple(source.strip() for source in evidence_sources),
                breadth_note=_require_text(
                    item.get("breadth_note"), f"proposed_categories[{index}].breadth_note"
                ),
                status=status,
            )
        )

    excluded_raw = interest.get("excluded_category_ids", [])
    if not isinstance(excluded_raw, list) or not all(
        isinstance(item, str) for item in excluded_raw
    ):
        raise ConfigError("interest.excluded_category_ids must be a list of category IDs")
    overlap = proposed_ids.intersection(excluded_raw)
    if overlap:
        raise ConfigError(f"proposed categories are also excluded: {sorted(overlap)}")

    schedule = _require_mapping(root.get("schedule", {}), "schedule")
    catch_up_missed_windows = schedule.get("catch_up_missed_windows")
    if catch_up_missed_windows is None:
        catch_up_missed_windows = schedule.get("catch_up_after_login", True)
    failure_notification = str(schedule.get("failure_notification", "codex")).casefold()
    if failure_notification != "codex":
        raise ConfigError("schedule.failure_notification must be 'codex'")

    taxonomy = _require_mapping(root.get("taxonomy"), "taxonomy")
    sources = _require_mapping(root.get("sources"), "sources")
    feeds_payload = sources.get("philpapers_category_pages")
    if not isinstance(feeds_payload, list) or not feeds_payload:
        raise ConfigError("sources.philpapers_category_pages must be a non-empty list")
    feeds = []
    feed_ids: set[str] = set()
    for index, raw_item in enumerate(feeds_payload):
        item = _require_mapping(raw_item, f"philpapers_category_pages[{index}]")
        category_id = _require_text(item.get("category_id"), f"feed[{index}].category_id")
        if category_id in feed_ids:
            raise ConfigError(f"duplicate feed category_id: {category_id}")
        feed_ids.add(category_id)
        feeds.append(
            FeedConfig(
                category_id=category_id,
                category_name=_require_text(
                    item.get("category_name"), f"feed[{index}].category_name"
                ),
                url=_require_text(item.get("url"), f"feed[{index}].url"),
            )
        )

    philarchive_oai = _require_mapping(
        sources.get("philarchive_oai", {}), "sources.philarchive_oai"
    )
    crossref = _require_mapping(sources.get("crossref", {}), "sources.crossref")
    openalex = _require_mapping(sources.get("openalex", {}), "sources.openalex")
    retry = _require_mapping(root.get("retry", {}), "retry")
    max_attempts = int(retry.get("max_unresolved_attempts", 8))
    retry_days = int(retry.get("unresolved_retry_days", 7))
    if max_attempts < 1 or retry_days < 1:
        raise ConfigError("retry values must be positive integers")

    privacy = _require_mapping(root.get("privacy"), "privacy")
    if privacy.get("retain_full_text") is not False:
        raise ConfigError("privacy.retain_full_text must be false in version 0.1")
    if privacy.get("log_original_research_text") is not False:
        raise ConfigError("privacy.log_original_research_text must be false in version 0.1")

    storage = _require_mapping(root.get("storage"), "storage")
    onboarding = _parse_onboarding(root, schema_version)
    return WatchlistConfig(
        path=config_path,
        timezone=timezone,
        report_weekday=WEEKDAYS[weekday_name],
        profile_id=_require_text(interest.get("profile_id"), "interest.profile_id"),
        profile_version=int(interest.get("version", 1)),
        profile_effective_from=_parse_optional_instant(
            interest.get("effective_from"), "interest.effective_from"
        ),
        original_text=_require_text(interest.get("original_text"), "interest.original_text"),
        confirmed_categories=tuple(confirmed),
        proposed_categories=tuple(proposed),
        inference_mode=inference_mode,
        excluded_category_ids=frozenset(excluded_raw),
        onboarding=onboarding,
        schedule=ScheduleConfig(
            local_time=_parse_local_time(
                schedule.get("local_time", "08:00"), "schedule.local_time"
            ),
            catch_up_missed_windows=bool(catch_up_missed_windows),
            defer_for_explicit_high_priority_work=bool(
                schedule.get("defer_for_explicit_high_priority_work", True)
            ),
            failure_notification=failure_notification,
        ),
        taxonomy_path=_resolve_project_path(
            taxonomy.get("path"), project_root=project_root, field="taxonomy.path"
        ),
        allow_fixture_for_dry_run=bool(taxonomy.get("allow_fixture_for_dry_run", False)),
        feeds=tuple(feeds),
        philarchive_oai=PhilArchiveOAIConfig(
            enabled=bool(philarchive_oai.get("enabled", False)),
            endpoint=_philarchive_oai_endpoint(
                philarchive_oai.get("endpoint", "https://philarchive.org/oai.pl"),
                "sources.philarchive_oai.endpoint",
            ),
        ),
        crossref_mailto=_optional_mailto(crossref.get("mailto"), "sources.crossref.mailto"),
        openalex_mailto=_optional_mailto(openalex.get("mailto"), "sources.openalex.mailto"),
        max_unresolved_attempts=max_attempts,
        unresolved_retry_days=retry_days,
        storage=StorageConfig(
            state_database=_resolve_project_path(
                storage.get("state_database"),
                project_root=project_root,
                field="storage.state_database",
            ),
            report_directory=_resolve_project_path(
                storage.get("report_directory"),
                project_root=project_root,
                field="storage.report_directory",
            ),
        ),
    )
