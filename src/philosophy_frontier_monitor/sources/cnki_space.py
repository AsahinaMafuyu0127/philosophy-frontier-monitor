"""Bounded CNKI Space metadata discovery based on the public search form.

The search parameters follow the cnki-search MCP's public method. This module
uses an ordinary HTTP client and never treats a search hit as publication proof.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

SEARCH_URL = "https://search.cnki.com.cn/Search/Result"
RESULT_URL = "https://search.cnki.com.cn/search/listresult"
ARTICLE_HOST = "www.cnki.com.cn"
ISSUE_PATTERN = re.compile(r"(?P<year>(?:19|20)\d{2})\s*年?\s*(?P<issue>\d{1,3}|S\d+)\s*期", re.I)
MONTH_PATTERN = re.compile(r"(?P<year>(?:19|20)\d{2})\s*年\s*(?P<month>0?[1-9]|1[0-2])\s*月")
PAGES_PATTERN = re.compile(r'hidTotalPageCount"\)\.val\("(?P<count>\d+)"\)')
CHALLENGE_MARKERS = ("安全验证", "验证码", "访问过于频繁", "blockPuzzle", "403 Forbidden")


class CnkiSpaceError(RuntimeError):
    """Source failure with a safe, stable reason code."""


@dataclass(frozen=True, slots=True)
class CnkiSearchTerm:
    query: str
    field: str = "title"


@dataclass(frozen=True, slots=True)
class CnkiRecord:
    title: str
    url: str
    authors: tuple[str, ...]
    venue: str
    year: int | None
    issue: str | None
    label_month: int | None
    date_evidence_urls: tuple[str, ...] = ()
    publication_date: str | None = None
    date_conflict: bool = False


@dataclass(frozen=True, slots=True)
class CnkiIssue:
    key: str
    venue: str
    year: int
    issue: str
    label_month: int | None
    records: tuple[CnkiRecord, ...]
    queries: tuple[str, ...]
    observed_at: datetime
    date_evidence_urls: tuple[str, ...] = ()
    publication_date: str | None = None
    date_conflict: bool = False


@dataclass(frozen=True, slots=True)
class CnkiScan:
    issues: tuple[CnkiIssue, ...]
    records: tuple[CnkiRecord, ...]
    checked_at: datetime
    status: str
    requested_pages: int
    result_pages: int
    incomplete_queries: tuple[str, ...]
    failures: tuple[str, ...]


def _clean(value: str) -> str:
    return " ".join(value.split())


def _article_url(value: str) -> str | None:
    url = f"https:{value}" if value.startswith("//") else value
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ARTICLE_HOST
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not re.fullmatch(r"/Article/CJFDTOTAL-[A-Za-z0-9]+\.htm", parsed.path, re.I)
        or parsed.query
        or parsed.fragment
    ):
        return None
    return url


class _ListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[CnkiRecord] = []
        self._item: dict[str, object] | None = None
        self._depth = 0
        self._section = ""
        self._anchor = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div":
            if self._item is not None:
                self._depth += 1
            elif "list-item" in classes:
                self._item = {
                    "title": "",
                    "url": "",
                    "authors": [],
                    "venue": "",
                    "source": [],
                    "issue_label": "",
                }
                self._depth = 1
            return
        if self._item is None:
            return
        if tag == "p":
            self._section = "title" if "tit" in classes else "source" if "source" in classes else ""
        elif tag == "a":
            href = attributes.get("href") or ""
            if self._section == "title" and not self._item["url"]:
                url = _article_url(href)
                if url:
                    self._item["url"] = url
                    self._anchor = "title"
                    self._text = []
            elif self._section == "source":
                if attributes.get("data-key") and "author=" in href:
                    self._anchor = "author"
                    self._text = []
                elif "RedirectSpace" in href and not self._item["venue"]:
                    self._anchor = "venue"
                    self._text = []
                elif "RedirectSpace" in href:
                    self._anchor = "issue_label"
                    self._text = []

    def handle_data(self, data: str) -> None:
        if self._item is None:
            return
        if self._section == "source":
            source = self._item["source"]
            assert isinstance(source, list)
            source.append(data)
        if self._anchor:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._item is None:
            return
        if tag == "a" and self._anchor:
            value = _clean("".join(self._text))
            if self._anchor == "title":
                self._item["title"] = re.sub(r"\s*CNKI(?:阅读|文献)\s*$", "", value)
            elif self._anchor == "venue":
                self._item["venue"] = value.strip("《》")
            elif self._anchor == "issue_label":
                self._item["issue_label"] = value
            elif self._anchor == "author" and value:
                authors = self._item["authors"]
                assert isinstance(authors, list)
                if value not in authors:
                    authors.append(value)
            self._anchor = ""
            self._text = []
        elif tag == "p":
            self._section = ""
        elif tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._finish_item()
                self._item = None

    def _finish_item(self) -> None:
        assert self._item is not None
        title = str(self._item["title"])
        url = str(self._item["url"])
        venue = str(self._item["venue"])
        source = _clean("".join(self._item["source"]))  # type: ignore[arg-type]
        issue_label = str(self._item["issue_label"])
        if not title or not url or not venue or "期刊" not in source:
            return
        issue_match = ISSUE_PATTERN.search(issue_label)
        month_match = MONTH_PATTERN.search(issue_label)
        year = (
            int(issue_match["year"])
            if issue_match
            else int(month_match["year"])
            if month_match
            else None
        )
        issue = issue_match["issue"].upper().lstrip("0") if issue_match else None
        if issue is None and month_match:
            issue = f"{int(month_match['month'])}月"
        authors = self._item["authors"]
        assert isinstance(authors, list)
        self.records.append(
            CnkiRecord(
                title=title,
                url=url,
                authors=tuple(authors),
                venue=venue,
                year=year,
                issue=issue,
                label_month=int(month_match["month"]) if month_match else None,
            )
        )


def parse_result_page(html: str) -> tuple[tuple[CnkiRecord, ...], int]:
    """Parse the list fragment and require a total-page signal for safe pagination."""

    if any(marker in html for marker in CHALLENGE_MARKERS):
        raise CnkiSpaceError("blocked")
    pages_match = PAGES_PATTERN.search(html)
    if pages_match is None:
        raise CnkiSpaceError("pagination_missing")
    total_pages = int(pages_match["count"])
    parser = _ListParser()
    parser.feed(html)
    records = tuple({record.url: record for record in parser.records}.values())
    if total_pages and not records:
        raise CnkiSpaceError("records_missing")
    if not total_pages and records:
        raise CnkiSpaceError("pagination_inconsistent")
    return records, total_pages


def fetch_result_page(
    client: httpx.Client, term: CnkiSearchTerm, *, year: int | None, page: int
) -> tuple[tuple[CnkiRecord, ...], int]:
    if term.field not in {"title", "theme"} or not 1 <= page <= 50:
        raise ValueError("invalid CNKI search field or page")
    try:
        warmup = client.get(SEARCH_URL, params={"content": term.query})
        if warmup.status_code == 403:
            raise CnkiSpaceError("blocked")
        if warmup.status_code >= 400:
            raise CnkiSpaceError(f"http_{warmup.status_code}")
        if warmup.status_code >= 300 or urlsplit(str(warmup.url)).hostname != "search.cnki.com.cn":
            raise CnkiSpaceError("unexpected_redirect")
        if any(marker in warmup.text for marker in CHALLENGE_MARKERS):
            raise CnkiSpaceError("blocked")
        payload: dict[str, str | int] = {
            "searchType": "MulityTermsSearch",
            "Title" if term.field == "title" else "Theme": term.query,
            "Type": 1,
            "ArticleType": 1,
            "Page": page,
            "Order": 2,
        }
        if year is not None:
            payload["Year"] = year
        response = client.post(
            RESULT_URL,
            data=payload,
            headers={
                "Accept": "text/html, */*; q=0.01",
                "Origin": "https://search.cnki.com.cn",
                "Referer": "https://search.cnki.com.cn/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if response.status_code == 403:
            raise CnkiSpaceError("blocked")
        if response.status_code >= 400:
            raise CnkiSpaceError(f"http_{response.status_code}")
    except httpx.HTTPError as error:
        raise CnkiSpaceError(f"http_{type(error).__name__}") from error
    if urlsplit(str(response.url)).hostname != "search.cnki.com.cn":
        raise CnkiSpaceError("unexpected_redirect")
    if "text/html" not in response.headers.get("content-type", "").lower():
        raise CnkiSpaceError("unexpected_content_type")
    return parse_result_page(response.text)


def _issue_key(venue: str, year: int, issue: str) -> str:
    normalized = unicodedata.normalize("NFKC", venue).casefold()
    normalized = re.sub(r"\s+", "", normalized)
    return hashlib.sha256(f"{normalized}|{year}|{issue}".encode()).hexdigest()


def _consistent_issue_month(records: list[CnkiRecord]) -> int | None:
    months = {record.label_month for record in records if record.label_month is not None}
    return next(iter(months)) if len(months) == 1 else None


def scan_cnki_space(
    terms: tuple[CnkiSearchTerm, ...],
    *,
    years: tuple[int | None, ...],
    max_pages: int,
    checked_at: datetime,
    client: httpx.Client | None = None,
) -> CnkiScan:
    """Discover a bounded set of CNKI records and issue labels, never full coverage."""

    own_client = client is None
    active_client = client or httpx.Client(timeout=20, follow_redirects=False)
    all_records: dict[str, CnkiRecord] = {}
    issue_groups: dict[str, list[CnkiRecord]] = {}
    issue_queries: dict[str, set[str]] = {}
    incomplete: list[str] = []
    failures: list[str] = []
    requested_pages = 0
    result_pages = 0
    try:
        for term in terms:
            for year in years:
                for page in range(1, max_pages + 1):
                    requested_pages += 1
                    try:
                        records, total_pages = fetch_result_page(
                            active_client, term, year=year, page=page
                        )
                    except CnkiSpaceError as error:
                        failures.append(f"{term.field}:{year}:{page}:{error}")
                        break
                    result_pages += 1
                    for record in records:
                        if year is not None and record.year != year:
                            continue
                        all_records.setdefault(record.url, record)
                        if not record.issue or not record.venue:
                            continue
                        if record.year is None:
                            continue
                        key = _issue_key(record.venue, record.year, record.issue)
                        group = issue_groups.setdefault(key, [])
                        if all(existing.url != record.url for existing in group):
                            group.append(record)
                        issue_queries.setdefault(key, set()).add(term.query)
                    if page >= total_pages:
                        break
                    if page == max_pages:
                        incomplete.append(f"{term.field}:{year}:page_limit")
    finally:
        if own_client:
            active_client.close()
    issues = tuple(
        CnkiIssue(
            key=key,
            venue=group[0].venue,
            year=group[0].year or 0,
            issue=group[0].issue or "",
            label_month=_consistent_issue_month(group),
            records=tuple(group),
            queries=tuple(sorted(issue_queries[key])),
            observed_at=checked_at,
        )
        for key, group in sorted(issue_groups.items())
    )
    status = "failed" if not result_pages else "partial" if failures or incomplete else "success"
    return CnkiScan(
        issues=issues,
        records=tuple(all_records.values()),
        checked_at=checked_at,
        status=status,
        requested_pages=requested_pages,
        result_pages=result_pages,
        incomplete_queries=tuple(incomplete),
        failures=tuple(failures),
    )
