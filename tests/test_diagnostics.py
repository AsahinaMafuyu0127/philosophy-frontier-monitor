from philosophy_frontier_monitor import diagnostics


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
