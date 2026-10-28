import json
from types import SimpleNamespace

from philosophy_frontier_monitor import cli, diagnostics
from philosophy_frontier_monitor.config import FeedConfig
from philosophy_frontier_monitor.http_retry import BoundedRequestError
from philosophy_frontier_monitor.sources.philpapers_rss import PhilPapersFeedError


def _network_pass(source, host):
    return diagnostics.DiagnosticCheck(f"{source}-network", source, "network", "pass", "ok")


def test_diagnose_sources_keeps_dns_tls_and_http_contract_separate(monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "_check_dns",
        lambda source, _host: diagnostics.DiagnosticCheck(
            f"{source}-dns", source, "dns", "pass", "dns ok"
        ),
    )
    monkeypatch.setattr(
        diagnostics,
        "_check_tls",
        lambda source, _host: diagnostics.DiagnosticCheck(
            f"{source}-tls", source, "tls", "fail", "tls failed"
        ),
    )
    monkeypatch.setitem(diagnostics.PROBES, "openalex", lambda _url: "contract ok")

    checks = diagnostics.diagnose_sources(
        ("openalex",),
        category_url="https://philpapers.org/browse/plato-theaetetus",
    )

    assert [item.layer for item in checks] == ["dns", "tls", "http_contract"]
    assert [item.status for item in checks] == ["pass", "fail", "pass"]


def test_openalex_probe_reports_key_presence_without_exposing_key(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "private-test-key")
    captured = {}

    def fake_request_json(**kwargs):
        captured.update(kwargs)
        return {"meta": {"count": 1}, "results": [{"id": "https://openalex.org/W1"}]}

    monkeypatch.setattr(diagnostics, "_request_json", fake_request_json)

    detail = diagnostics._probe_openalex()

    assert "布尔状态报告：是" in detail
    assert "private-test-key" not in detail
    assert captured["params"]["api_key"] == "private-test-key"


def test_contract_failure_reports_exception_class_not_response_content(monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "_check_dns",
        lambda source, _host: diagnostics.DiagnosticCheck(
            f"{source}-dns", source, "dns", "pass", "dns ok"
        ),
    )
    monkeypatch.setattr(
        diagnostics,
        "_check_tls",
        lambda source, _host: diagnostics.DiagnosticCheck(
            f"{source}-tls", source, "tls", "pass", "tls ok"
        ),
    )

    def invalid(_url):
        raise ValueError("response contained private-test-key")

    monkeypatch.setitem(diagnostics.PROBES, "crossref", invalid)

    checks = diagnostics.diagnose_sources(
        ("crossref",),
        category_url="https://philpapers.org/browse/plato-theaetetus",
    )

    contract = checks[-1]
    assert contract.status == "fail"
    assert contract.detail == "响应合约检查失败：ValueError。"
    assert "private-test-key" not in contract.detail


def test_configured_philpapers_diagnostic_checks_every_actual_feed_without_exposing_urls(
    monkeypatch,
):
    feeds = (
        FeedConfig("6518", "private category one", "https://philpapers.org/browse/one", "https://philpapers.org/browse/one?dg=private-one"),
        FeedConfig("243942", "private category two", "https://philpapers.org/browse/two", "https://philpapers.org/browse/two?dg=private-two"),
    )
    observed = []
    monkeypatch.setattr(diagnostics, "_check_dns", _network_pass)
    monkeypatch.setattr(diagnostics, "_check_tls", _network_pass)

    def load(feed, checked_at):
        observed.append(feed)
        return SimpleNamespace(entries=("record",))

    monkeypatch.setattr(diagnostics, "load_philpapers_feed", load)
    checks = diagnostics.diagnose_sources(
        ("philpapers",), category_url="https://philpapers.org/browse/sample", configured_feeds=feeds
    )

    assert observed == list(feeds)
    assert [item.check for item in checks[-2:]] == [
        "philpapers-feed-1-http-contract", "philpapers-feed-2-http-contract"
    ]
    assert [item.status for item in checks] == ["pass"] * 4
    output = json.dumps([item.as_dict() for item in checks], ensure_ascii=False)
    assert "private-one" not in output
    assert "private-two" not in output
    assert "private category" not in output
    assert "6518" not in output


def test_configured_philpapers_diagnostic_reports_403_without_private_url(monkeypatch):
    feed = FeedConfig(
        "6518", "private category", "https://philpapers.org/browse/one",
        "https://philpapers.org/browse/one?dg=private-token",
    )

    def denied(_feed, _checked_at):
        try:
            raise BoundedRequestError(
                source="PhilPapers", failure_kind="permanent_http", attempts=1,
                stop_reason="non_retryable_status", status_code=403,
            )
        except BoundedRequestError as error:
            raise PhilPapersFeedError("private-token") from error

    monkeypatch.setattr(diagnostics, "load_philpapers_feed", denied)
    check = diagnostics._diagnose_configured_philpapers_feed(feed, 1)

    assert check.status == "fail"
    assert "HTTP 403" in check.detail
    assert "private-token" not in json.dumps(check.as_dict(), ensure_ascii=False)


def test_doctor_passes_private_feed_config_to_live_diagnostic_without_echoing_links(
    monkeypatch, capsys
):
    observed = {}

    def diagnose(sources, **kwargs):
        observed.update(kwargs)
        return [
            diagnostics.DiagnosticCheck(
                "philpapers-feed-1-http-contract", "philpapers", "http_contract", "pass", "ok"
            )
        ]

    monkeypatch.setattr(cli, "diagnose_sources", diagnose)
    assert cli.main([
        "doctor", "--live", "--source", "philpapers", "--feeds-config",
        "tests/fixtures/watchlist_minimal.yaml",
    ]) == 0
    output = capsys.readouterr().out
    assert observed["configured_feeds"] is not None
    assert len(observed["configured_feeds"]) == 1
    assert "official_rss_url" not in output


def test_doctor_returns_failure_when_configured_feed_probe_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "diagnose_sources",
        lambda _sources, **_kwargs: [
            diagnostics.DiagnosticCheck(
                "philpapers-feed-1-http-contract",
                "philpapers",
                "http_contract",
                "fail",
                "已配置分类 1 订阅读取失败：HTTP 403；尝试 1 次。",
            )
        ],
    )

    assert cli.main(
        [
            "doctor",
            "--live",
            "--source",
            "philpapers",
            "--feeds-config",
            "tests/fixtures/watchlist_minimal.yaml",
        ]
    ) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["state_mutated"] is False
