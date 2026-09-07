import json
import shutil
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import philosophy_frontier_monitor.pipeline as pipeline
from philosophy_frontier_monitor.sources.philpapers_taxonomy import (
    API_ID_ENV,
    API_KEY_ENV,
    API_URL,
    PhilPapersCredentials,
    PhilPapersTaxonomyError,
    TaxonomyContractError,
    TaxonomyCredentialError,
    credentials_from_environment,
    credentials_from_file,
    fetch_and_write_taxonomy,
    fetch_taxonomy_bytes,
    import_and_write_taxonomy,
    load_credentials,
    normalize_taxonomy_payload,
    write_taxonomy_snapshot,
)
from philosophy_frontier_monitor.taxonomy import (
    TaxonomyError,
    find_all_by_name,
    find_by_name,
    load_taxonomy,
    require_production_taxonomy,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def output_directory():
    path = PROJECT_ROOT / "var" / "test-output" / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _small_documented_feed() -> bytes:
    # The official documentation lists positional fields as name, ID,
    # comma-separated parents, and primary parent. Root ID 1 is omitted.
    return json.dumps(
        [
            ["Epistemology", 11, "1", 1],
            ["Plato: Epistemology", 74810, "1,11", 11],
        ]
    ).encode()


def test_credentials_are_read_only_from_named_environment_variables():
    credentials = credentials_from_environment(
        {API_ID_ENV: "local-user", API_KEY_ENV: "secret-key"}
    )

    assert credentials.api_id == "local-user"
    assert credentials.api_key == "secret-key"
    assert "local-user" not in repr(credentials)
    assert "secret-key" not in repr(credentials)


def test_missing_credentials_names_variables_without_printing_values():
    with pytest.raises(TaxonomyCredentialError, match=API_KEY_ENV) as caught:
        credentials_from_environment({API_ID_ENV: "do-not-print"})

    assert "do-not-print" not in str(caught.value)


def test_reads_user_friendly_private_credential_file(output_directory):
    path = output_directory / "credentials.txt"
    path.write_text("API KEY secret-key\nUSER ID ： local-user\n", encoding="utf-8")

    credentials = credentials_from_file(path)

    assert credentials.api_id == "local-user"
    assert credentials.api_key == "secret-key"
    assert "secret-key" not in repr(credentials)


def test_environment_pair_takes_precedence_over_private_file(output_directory):
    path = output_directory / "credentials.txt"
    path.write_text("API KEY file-key\nUSER ID : file-user\n", encoding="utf-8")

    credentials = load_credentials(
        environ={API_ID_ENV: "environment-user", API_KEY_ENV: "environment-key"},
        credential_file=path,
    )

    assert credentials.api_id == "environment-user"
    assert credentials.api_key == "environment-key"


def test_malformed_private_file_error_never_echoes_its_value(output_directory):
    path = output_directory / "credentials.txt"
    path.write_text("UNKNOWN private-secret-value\n", encoding="utf-8")

    with pytest.raises(TaxonomyCredentialError) as caught:
        credentials_from_file(path)

    assert "private-secret-value" not in str(caught.value)


def test_authenticated_request_uses_fixed_url_and_returns_json_bytes():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        seen["accept"] = request.headers.get("accept")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=_small_documented_feed(),
        )

    with _client(handler) as client:
        result = fetch_taxonomy_bytes(
            PhilPapersCredentials("local-user", "secret-key"), client=client
        )

    assert result == _small_documented_feed()
    assert seen["url"].copy_with(query=None) == httpx.URL(API_URL)
    assert seen["url"].params["apiId"] == "local-user"
    assert seen["url"].params["apiKey"] == "secret-key"
    assert seen["accept"] == "application/json"


def test_http_error_suppresses_credential_bearing_request_url():
    def handler(request):
        raise httpx.ConnectError("failure includes " + str(request.url), request=request)

    with (
        _client(handler) as client,
        pytest.raises(PhilPapersTaxonomyError) as caught,
    ):
        fetch_taxonomy_bytes(PhilPapersCredentials("private-id", "private-key"), client=client)

    message = str(caught.value)
    assert "private-id" not in message
    assert "private-key" not in message
    assert API_URL not in message


def test_html_challenge_is_rejected_without_writing_or_parsing():
    def handler(request):
        return httpx.Response(403, headers={"Content-Type": "text/html"}, text="<html></html>")

    with (
        _client(handler) as client,
        pytest.raises(PhilPapersTaxonomyError, match="HTTP 403"),
    ):
        fetch_taxonomy_bytes(PhilPapersCredentials("id", "key"), client=client)


def test_cloudflare_challenge_is_reported_separately_from_bad_credentials():
    def handler(request):
        return httpx.Response(
            403,
            headers={"Content-Type": "text/html", "cf-mitigated": "challenge"},
            text="<html></html>",
        )

    with (
        _client(handler) as client,
        pytest.raises(PhilPapersTaxonomyError, match="Cloudflare challenged"),
    ):
        fetch_taxonomy_bytes(PhilPapersCredentials("id", "key"), client=client)


def test_redirect_is_rejected_even_when_supplied_client_would_follow_it():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://example.com/collect"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with client, pytest.raises(PhilPapersTaxonomyError, match="redirect"):
        fetch_taxonomy_bytes(PhilPapersCredentials("id", "key"), client=client)


def test_declared_or_streamed_oversize_response_is_rejected():
    def handler(request):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Length": "999"},
            content=b"[]",
        )

    with _client(handler) as client, pytest.raises(TaxonomyContractError, match="size limit"):
        fetch_taxonomy_bytes(
            PhilPapersCredentials("id", "key"),
            client=client,
            maximum_bytes=10,
        )


def test_normalizer_removes_only_the_documented_omitted_root_reference():
    payload = normalize_taxonomy_payload(
        _small_documented_feed(),
        retrieved_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        minimum_categories=2,
    )

    assert payload["omitted_root_id"] == "1"
    assert payload["source_url"] == API_URL
    assert "apiId" not in payload["source_url"]
    assert payload["categories"][0]["parent_ids"] == []
    assert payload["categories"][0]["primary_parent_id"] is None
    assert payload["categories"][1]["parent_ids"] == ["11"]


def test_normalizer_accepts_explicit_object_fields():
    raw = json.dumps(
        {
            "categories": [
                {"name": "Epistemology", "id": "11", "parents": "1", "primaryParent": "1"},
                {
                    "name": "Plato: Epistemology",
                    "id": "74810",
                    "parents": "1,11",
                    "primaryParent": "11",
                },
            ]
        }
    ).encode()

    payload = normalize_taxonomy_payload(raw, minimum_categories=2)

    assert payload["category_count"] == 2


def test_partial_feed_is_rejected_by_production_threshold():
    with pytest.raises(TaxonomyContractError, match="at least 2000"):
        normalize_taxonomy_payload(_small_documented_feed())


def test_fetch_validates_then_atomically_writes_credential_free_snapshot(output_directory):
    def handler(request):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=_small_documented_feed(),
        )

    with _client(handler) as client:
        result = fetch_and_write_taxonomy(
            output_directory,
            PhilPapersCredentials("private-id", "private-key"),
            client=client,
            retrieved_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
            minimum_categories=2,
        )

    snapshot = load_taxonomy(result.path)
    saved_text = result.path.read_text(encoding="utf-8")
    assert snapshot.category_count == 2
    assert snapshot.omitted_root_id == "1"
    assert snapshot.source_content_hash.startswith("sha256:")
    assert "private-id" not in saved_text
    assert "private-key" not in saved_text
    assert not list(output_directory.glob("*.tmp"))


def test_existing_historical_snapshot_is_idempotent_but_never_overwritten(output_directory):
    payload = normalize_taxonomy_payload(
        _small_documented_feed(),
        retrieved_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        minimum_categories=2,
    )
    first = write_taxonomy_snapshot(payload, output_directory, minimum_categories=2)
    original_bytes = first.path.read_bytes()

    repeated = write_taxonomy_snapshot(payload, output_directory, minimum_categories=2)
    assert repeated.path == first.path
    assert repeated.snapshot_id == first.snapshot_id

    changed_same_identity = dict(payload)
    changed_same_identity["notes"] = "Different normalized content with the same snapshot ID."
    with pytest.raises(TaxonomyContractError, match="collision"):
        write_taxonomy_snapshot(
            changed_same_identity,
            output_directory,
            minimum_categories=2,
        )

    assert first.path.read_bytes() == original_bytes


def test_historical_profile_resolves_its_retained_taxonomy_by_snapshot_id(output_directory):
    old_payload = normalize_taxonomy_payload(
        _small_documented_feed(),
        retrieved_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        minimum_categories=2,
    )
    old_result = write_taxonomy_snapshot(old_payload, output_directory, minimum_categories=2)
    new_raw = json.dumps(
        [
            ["Epistemology", 11, "1", 1],
            ["Plato: Epistemology", 74810, "1,11", 11],
            ["A New Unrelated Category", 99999, "1", 1],
        ]
    ).encode()
    new_payload = normalize_taxonomy_payload(
        new_raw,
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        minimum_categories=2,
    )
    new_result = write_taxonomy_snapshot(new_payload, output_directory, minimum_categories=2)
    old_snapshot = load_taxonomy(old_result.path)
    new_snapshot = load_taxonomy(new_result.path)

    resolved = pipeline._taxonomy_path_for_profile(
        new_result.path,
        new_snapshot,
        old_snapshot.snapshot_id,
    )

    assert resolved == old_result.path.resolve()


def test_invalid_snapshot_identifier_cannot_choose_an_output_path(output_directory):
    payload = normalize_taxonomy_payload(_small_documented_feed(), minimum_categories=2)
    payload["snapshot_id"] = "philpapers:..\\escape:bad"

    with pytest.raises(TaxonomyContractError, match="snapshot ID"):
        write_taxonomy_snapshot(payload, output_directory, minimum_categories=2)
    assert not list(output_directory.iterdir())


def test_production_snapshot_identity_must_match_source_content_hash(output_directory):
    payload = normalize_taxonomy_payload(_small_documented_feed(), minimum_categories=2)
    result = write_taxonomy_snapshot(payload, output_directory, minimum_categories=2)
    snapshot = load_taxonomy(result.path)
    mismatched = replace(
        snapshot,
        snapshot_id="philpapers:20260905T120000Z:000000000000",
    )

    with pytest.raises(TaxonomyError, match="source content hash"):
        require_production_taxonomy(mismatched)


def test_invalid_parent_graph_never_publishes_a_snapshot(output_directory):
    payload = normalize_taxonomy_payload(_small_documented_feed(), minimum_categories=2)
    payload["categories"][1]["parent_ids"] = ["999"]
    payload["categories"][1]["primary_parent_id"] = "999"

    with pytest.raises(TaxonomyError, match="missing parents"):
        write_taxonomy_snapshot(payload, output_directory, minimum_categories=2)
    assert not list(output_directory.iterdir())


def test_imports_browser_download_through_the_same_validation(output_directory):
    inbox = output_directory / "inbox"
    inbox.mkdir()
    raw_path = inbox / "categories.json"
    raw_path.write_bytes(_small_documented_feed())
    snapshot_directory = output_directory / "snapshots"

    result = import_and_write_taxonomy(
        raw_path,
        snapshot_directory,
        retrieved_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        minimum_categories=2,
    )

    assert result.path.is_file()
    assert result.category_count == 2
    assert raw_path.is_file()


def test_browser_import_rejects_html_error_page(output_directory):
    raw_path = output_directory / "categories.json"
    raw_path.write_text("<html>Cloudflare challenge</html>", encoding="utf-8")

    with pytest.raises(TaxonomyContractError, match="UTF-8 JSON"):
        import_and_write_taxonomy(raw_path, output_directory / "snapshots")
    assert not (output_directory / "snapshots").exists()


def test_excludes_only_unreferenced_empty_leaf_and_audits_duplicate_names(
    output_directory,
):
    raw = json.dumps(
        [
            ["Duplicate Name", 10, "1", 1],
            ["Duplicate Name", 11, "1", 1],
            ["", 12, "10", 10],
        ]
    ).encode()

    payload = normalize_taxonomy_payload(raw, minimum_categories=2)
    result = write_taxonomy_snapshot(payload, output_directory, minimum_categories=2)
    snapshot = load_taxonomy(result.path)

    assert payload["source_record_count"] == 3
    assert payload["category_count"] == 2
    assert payload["excluded_source_records"] == [
        {"category_id": "12", "reason": "empty_category_name_unreferenced_leaf"}
    ]
    assert payload["duplicate_name_groups"] == [
        {"category_name": "Duplicate Name", "category_ids": ["10", "11"]}
    ]
    assert find_by_name(snapshot, "Duplicate Name") is None
    assert {item.category_id for item in find_all_by_name(snapshot, "Duplicate Name")} == {
        "10",
        "11",
    }


def test_rejects_empty_name_when_other_categories_depend_on_it():
    raw = json.dumps(
        [
            ["", 10, "1", 1],
            ["Named Child", 11, "10", 10],
        ]
    ).encode()

    with pytest.raises(TaxonomyContractError, match="referenced as a parent"):
        normalize_taxonomy_payload(raw, minimum_categories=2)
