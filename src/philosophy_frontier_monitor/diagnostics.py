"""Read-only, credential-safe network diagnostics for runtime providers."""

from __future__ import annotations

import os
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from .http_retry import BoundedRequestError, request_with_retry
from .sources.crossref import API_URL as CROSSREF_API_URL
from .sources.crossref import DEFAULT_USER_AGENT as CROSSREF_USER_AGENT
from .sources.openalex import API_URL as OPENALEX_API_URL
from .sources.openalex import DEFAULT_USER_AGENT as OPENALEX_USER_AGENT
from .sources.philarchive_oai import DEFAULT_ENDPOINT as PHILARCHIVE_ENDPOINT
from .sources.philarchive_oai import DEFAULT_USER_AGENT as PHILARCHIVE_USER_AGENT
from .sources.philarchive_oai import OAI_NS
from .sources.philpapers_rss import discover_feed_request, fetch_feed, parse_feed


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    check: str
    source: str
    layer: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "source": self.source,
            "layer": self.layer,
            "status": self.status,
            "detail": self.detail,
        }


SOURCE_HOSTS = {
    "philpapers": "philpapers.org",
    "philarchive": "philarchive.org",
    "openalex": "api.openalex.org",
    "crossref": "api.crossref.org",
}


def _check_dns(source: str, host: str) -> DiagnosticCheck:
    try:
        addresses = {
            item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM) if item[4]
        }
    except OSError:
        return DiagnosticCheck(
            f"{source}-dns",
            source,
            "dns",
            "fail",
            "域名解析失败；检查本机 DNS、网络与代理设置。",
        )
    return DiagnosticCheck(
        f"{source}-dns",
        source,
        "dns",
        "pass",
        f"解析到 {len(addresses)} 个不重复地址。",
    )


def _check_tls(source: str, host: str) -> DiagnosticCheck:
    try:
        context = ssl.create_default_context()
        with (
            socket.create_connection((host, 443), timeout=10) as connection,
            context.wrap_socket(connection, server_hostname=host) as secured,
        ):
            version = secured.version() or "unknown"
    except (OSError, ssl.SSLError):
        return DiagnosticCheck(
            f"{source}-tls",
            source,
            "tls",
            "fail",
            "TLS 握手或证书校验失败；未绕过证书验证。",
        )
    return DiagnosticCheck(
        f"{source}-tls",
        source,
        "tls",
        "pass",
        f"证书校验与握手通过；协议 {version}。",
    )


def _request_json(
    *,
    source: str,
    url: str,
    params: dict[str, str],
    user_agent: str,
) -> dict[str, Any]:
    with httpx.Client(
        headers={"User-Agent": user_agent},
        follow_redirects=True,
        timeout=30,
    ) as client:
        response = request_with_retry(
            lambda: client.get(url, params=params),
            source=source,
        )
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("response root is not an object")
    return payload


def _probe_philpapers(category_url: str) -> str:
    request = discover_feed_request(category_url)
    entries = parse_feed(fetch_feed(request))
    return f"HTTP 与 feed 合约通过；cId={request.category_id}；条目数={len(entries)}。"


def _probe_openalex() -> str:
    params = {
        "filter": "openalex:W2741809807",
        "per_page": "1",
        "select": "id",
    }
    api_key = os.environ.get("OPENALEX_API_KEY")
    if api_key:
        params["api_key"] = api_key
    payload = _request_json(
        source="OpenAlex",
        url=OPENALEX_API_URL,
        params=params,
        user_agent=OPENALEX_USER_AGENT,
    )
    if not isinstance(payload.get("results"), list) or not isinstance(payload.get("meta"), dict):
        raise ValueError("missing OpenAlex meta/results fields")
    return "HTTP 与 JSON 合约通过；API key 是否存在仅以布尔状态报告：" + (
        "是。" if api_key else "否。"
    )


def _probe_crossref() -> str:
    payload = _request_json(
        source="Crossref",
        url=CROSSREF_API_URL,
        params={"rows": "0"},
        user_agent=CROSSREF_USER_AGENT,
    )
    if payload.get("status") != "ok" or not isinstance(payload.get("message"), dict):
        raise ValueError("missing Crossref status/message fields")
    return "HTTP 与 JSON 合约通过；探测请求未拉取书目记录。"


def _probe_philarchive() -> str:
    with httpx.Client(
        headers={"User-Agent": PHILARCHIVE_USER_AGENT},
        follow_redirects=True,
        timeout=30,
    ) as client:
        response = request_with_retry(
            lambda: client.get(PHILARCHIVE_ENDPOINT, params={"verb": "Identify"}),
            source="PhilArchive OAI",
        )
    root = ElementTree.fromstring(response.text)
    if root.tag != f"{{{OAI_NS}}}OAI-PMH" or root.find(f"{{{OAI_NS}}}Identify") is None:
        raise ValueError("missing OAI-PMH Identify element")
    return "HTTP、XML 与 OAI-PMH Identify 合约通过。"


PROBES = {
    "philpapers": _probe_philpapers,
    "philarchive": lambda _category_url: _probe_philarchive(),
    "openalex": lambda _category_url: _probe_openalex(),
    "crossref": lambda _category_url: _probe_crossref(),
}


def diagnose_sources(
    sources: tuple[str, ...],
    *,
    category_url: str,
) -> list[DiagnosticCheck]:
    """Run DNS, TLS, HTTP and contract checks without mutating monitor state."""

    checks: list[DiagnosticCheck] = []
    for source in sources:
        host = SOURCE_HOSTS[source]
        checks.append(_check_dns(source, host))
        checks.append(_check_tls(source, host))
        try:
            detail = PROBES[source](category_url)
        except BoundedRequestError as error:
            detail = str(error)
            status = "fail"
        except (httpx.HTTPError, OSError, ValueError, ElementTree.ParseError) as error:
            detail = f"响应合约检查失败：{type(error).__name__}。"
            status = "fail"
        except Exception as error:  # pragma: no cover - defensive diagnostic boundary
            detail = f"探测失败：{type(error).__name__}。"
            status = "fail"
        else:
            status = "pass"
        checks.append(
            DiagnosticCheck(
                f"{source}-http-contract",
                source,
                "http_contract",
                status,
                detail,
            )
        )
    return checks


def provider_host(source: str) -> str:
    """Expose the fixed host map for tests without accepting arbitrary hosts."""

    return urlparse(f"https://{SOURCE_HOSTS[source]}").hostname or SOURCE_HOSTS[source]
