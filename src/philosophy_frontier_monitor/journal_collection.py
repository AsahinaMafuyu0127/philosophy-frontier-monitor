"""Small, site-specific publisher TOC collection and censored database observations.

Three verified HTML layouts and one bounded publisher PDF layout are supported.
A search miss observes this public search surface, not an entire licensed database.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from pypdf import PdfReader

from .identity import IdentityMatchLevel, compare_bibliographic_identity
from .official_journals import CATALOG_PATH, OfficialIssue, list_issues, load_catalog, record_issue
from .sources.cnki_space import CnkiSearchTerm, CnkiSpaceError, fetch_result_page
from .sources.wanfang import (
    PAGE_ROWS,
    WanfangError,
    _read_key,
    fetch_query_page,
)

MAX_HTML_BYTES = 750_000
MAX_PDF_BYTES = 2_000_000
MAX_ARTICLES = 30
MAX_CHECKS = 10
CBPT_IDS = {
    "zhexue-yanjiu",
    "zhexue-dongtai",
    "zhexue-fenxi",
    "ziran-bianzhengfa-yanjiu",
    "kexue-jishu-zhexue-yanjiu",
}
SUPPORTED = {
    "ziran-bianzhengfa-tongxun", "zhouyi-yanjiu", "xiandai-zhexue", "kongzi-yanjiu",
} | CBPT_IDS


@dataclass(frozen=True, slots=True)
class PublisherArticle:
    journal_id: str
    year: int
    issue: str
    title: str
    author: str | None
    url: str
    issue_url: str
    first_observed_at: str


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href is not None:
            self.links.append((" ".join("".join(self.parts).split()), self.href))
            self.href = None


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return " ".join("".join(parser.parts).split())


def _links(html: str) -> list[tuple[str, str]]:
    parser = _Links()
    parser.feed(html)
    return parser.links


def _fetch(client: httpx.Client, url: str, host: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != host or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("publisher_url_outside_registered_host")
    try:
        with client.stream("GET", url) as response:
            if response.is_redirect or urlsplit(str(response.url)).hostname != host:
                raise ValueError("publisher_redirect")
            if response.status_code != 200:
                raise ValueError(f"publisher_http_{response.status_code}")
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise ValueError("publisher_not_html")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    raise ValueError("publisher_page_too_large")
                chunks.append(chunk)
            return b"".join(chunks).decode(response.charset_encoding or "utf-8", errors="replace")
    except httpx.TimeoutException as error:
        raise ValueError("publisher_timeout") from error
    except httpx.HTTPError as error:
        raise ValueError("publisher_transport_failed") from error


def _fetch_pdf(client: httpx.Client, url: str, host: str) -> bytes:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != host or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("publisher_pdf_outside_registered_host")
    try:
        with client.stream("GET", url) as response:
            if response.is_redirect or urlsplit(str(response.url)).hostname != host:
                raise ValueError("publisher_pdf_redirect")
            if response.status_code != 200:
                raise ValueError(f"publisher_pdf_http_{response.status_code}")
            if "application/pdf" not in response.headers.get("content-type", "").lower():
                raise ValueError("publisher_not_pdf")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ValueError("publisher_pdf_too_large")
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content.startswith(b"%PDF"):
                raise ValueError("publisher_invalid_pdf_header")
            return content
    except httpx.TimeoutException as error:
        raise ValueError("publisher_pdf_timeout") from error
    except httpx.HTTPError as error:
        raise ValueError("publisher_pdf_transport_failed") from error


def _sysu_index(html: str, url: str) -> tuple[int, str, str]:
    entries = []
    for label, href in _links(html):
        match = re.search(r"现代哲学\s*((?:19|20)\d{2})\s*年第\s*(\d{1,2})\s*期", label)
        if match and re.fullmatch(r"/article/\d+", href):
            entries.append((int(match[1]), str(int(match[2])), urljoin(url, href)))
    if not entries:
        raise ValueError("sysu_issue_link_missing")
    year, issue, issue_url = max(entries, key=lambda item: (item[0], int(item[1])))
    return year, issue, issue_url


def _sysu_article_page(html: str, url: str, host: str) -> tuple[str, str]:
    date_match = re.search(r"发布日期\s*[:：]\s*(\d{4}-\d{2}-\d{2})", _text(html))
    if date_match is None:
        raise ValueError("sysu_announcement_date_missing")
    pdf_urls = [
        urljoin(url, href) for label, href in _links(html)
        if "中文目录" in label and urlsplit(urljoin(url, href)).hostname == host
        and urlsplit(href).path.lower().endswith(".pdf")
    ]
    if len(pdf_urls) != 1:
        raise ValueError("sysu_chinese_toc_pdf_missing_or_ambiguous")
    return date_match[1], pdf_urls[0]


def _extract_pdf_text(content: bytes) -> tuple[str, int]:
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 3:
            raise ValueError("publisher_pdf_page_bound")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if not 100 <= len(text) <= 50_000:
            raise ValueError("publisher_pdf_text_missing_or_excessive")
        return text, len(reader.pages)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("publisher_pdf_unreadable") from error


def _sysu_toc(
    text: str, year: int, issue: str, pdf_url: str, observed: str
) -> tuple[str, list[PublisherArticle]]:
    heading = re.search(
        r"((?:19|20)\d{2})\s*年第\s*(\d{1,2})\s*期[^\n]{0,60}?/"
        r"(十一|十二|十|[一二三四五六七八九])月号", text
    )
    if heading is None or (int(heading[1]), int(heading[2])) != (year, int(issue)):
        raise ValueError("sysu_pdf_issue_heading_mismatch")
    month_names = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二")
    month = month_names.index(heading[3]) + 1
    leader = re.compile(r"(?:[·…]\s*){5,}")
    articles = []
    previous = ""
    for raw in text.splitlines():
        line = " ".join(raw.split())
        match = leader.search(line)
        if match is None:
            previous = line
            continue
        title = line[:match.start()].strip()
        if title.startswith("——") and previous:
            title = f"{previous}{title}"
        trailing = line[match.end():].strip()
        author_page = re.fullmatch(r"(.+?)\s+(\d{1,3})", trailing)
        if not author_page or not 4 <= len(title) <= 250:
            raise ValueError("sysu_pdf_article_line_unparsed")
        author = author_page[1].strip()
        if not author:
            raise ValueError("sysu_pdf_article_author_missing")
        articles.append(PublisherArticle(
            "xiandai-zhexue", year, issue, title, author, pdf_url, pdf_url, observed,
        ))
        previous = ""
    if not 5 <= len(articles) <= MAX_ARTICLES:
        raise ValueError("sysu_pdf_articles_missing_or_excessive")
    parameter = re.search(
        r"期刊基本参数[^\n]{0,160}\*(\d{1,2})\*((?:19|20)\d{2})-(\d{1,2})", text
    )
    if parameter is None:
        raise ValueError("sysu_pdf_article_count_missing")
    if (len(articles), year, month) != (
        int(parameter[1]), int(parameter[2]), int(parameter[3])
    ):
        raise ValueError("sysu_pdf_article_count_or_month_mismatch")
    return f"{year}-{month:02d}", articles


def _kongzi_index(html: str, url: str) -> tuple[int, str, str]:
    entries = []
    for label, href in _links(html):
        match = re.fullmatch(
            r"《孔子研究》\s*((?:19|20)\d{2})\s*年第\s*(\d{1,2})\s*期.*目录", label
        )
        if match and re.fullmatch(r"/content/6147_\d+\.html", href):
            entries.append((int(match[1]), str(int(match[2])), urljoin(url, href)))
    if not entries:
        raise ValueError("kongzi_issue_link_missing")
    return max(entries, key=lambda item: (item[0], int(item[1])))


def _kongzi_title_key(title: str) -> str:
    return re.sub(r"[\s：:]", "", title)


def _kongzi_toc(
    html: str, year: int, issue: str, url: str, observed: str,
) -> tuple[str, list[PublisherArticle]]:
    heading = re.search(r"<h3>《孔子研究》\s*((?:19|20)\d{2})\s*年第\s*"
                        r"(\d{1,2})\s*期[^<]*目录</h3>", html)
    if heading is None or (int(heading[1]), int(heading[2])) != (year, int(issue)):
        raise ValueError("kongzi_issue_heading_mismatch")
    header = re.search(r"</h3>\s*</div>\s*<h6>(.*?)</h6>", html, re.S)
    announced = re.search(r"((?:19|20)\d{2}-\d{2}-\d{2})", header[1]) if header else None
    if announced is None:
        raise ValueError("kongzi_announcement_date_missing")
    content = html.split('<div id="page-content">', 1)
    if len(content) != 2:
        raise ValueError("kongzi_toc_missing")
    paragraphs = [" ".join(_text(block).split()) for block in re.findall(
        r"<p\b[^>]*>(.*?)</p>", content[1], re.S | re.I,
    )]
    if not paragraphs or not re.fullmatch(r"●[^●]{1,100}●", paragraphs[0]):
        raise ValueError("kongzi_toc_start_missing")
    articles: list[PublisherArticle] = []
    end = None
    for pos, paragraph in enumerate(paragraphs):
        if articles and _kongzi_title_key(paragraph) == _kongzi_title_key(articles[0].title):
            end = pos
            break
        without_sections = re.sub(r"●[^●]{1,100}●", "", paragraph)
        for entry in re.split(r"\s+●\s+", without_sections):
            entry = entry.strip(" ●")
            if not entry or re.fullmatch(r"●[^●]{1,100}●", entry):
                continue
            match = re.fullmatch(r"(.{1,80}?)\s*[丨|]\s*(.{4,250})", entry)
            if match is None:
                raise ValueError("kongzi_toc_article_unparsed")
            articles.append(PublisherArticle(
                "kongzi-yanjiu", year, issue, match[2].strip(),
                " ".join(match[1].split()), url, url, observed,
            ))
            if len(articles) > MAX_ARTICLES:
                raise ValueError("kongzi_articles_exceed_bound")
    if end is None or len(articles) < 5:
        raise ValueError("kongzi_toc_end_or_articles_missing")
    echoed = {_kongzi_title_key(paragraph) for paragraph in paragraphs[end:]}
    if any(_kongzi_title_key(article.title) not in echoed for article in articles):
        raise ValueError("kongzi_toc_abstract_crosscheck_failed")
    return announced[1], articles


def _jdn_toc(html: str, url: str, observed: str) -> tuple[str, list[PublisherArticle]]:
    plain = _text(html)
    match = re.search(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*期", plain)
    if match is None:
        raise ValueError("jdn_issue_heading_missing")
    year, issue = int(match[1]), str(int(match[2]))
    links = _links(html)
    articles: list[PublisherArticle] = []
    for i, (title, href) in enumerate(links):
        if not re.fullmatch(r"/home/journal/view/id/\d+", href):
            continue
        author = None
        if i + 1 < len(links) and re.fullmatch(r"/home/retrieval/lists/id/\d+", links[i + 1][1]):
            author = links[i + 1][0] or None
        if 4 <= len(title) <= 250:
            articles.append(PublisherArticle(
                "ziran-bianzhengfa-tongxun", year, issue, title, author,
                urljoin(url, href), url.split("?", 1)[0], observed,
            ))
    if not articles or len(articles) > MAX_ARTICLES:
        raise ValueError("jdn_articles_missing_or_excessive")
    return issue, articles


def _sdu_index(html: str, url: str) -> tuple[int, str, str]:
    for label, href in _links(html):
        match = re.search(r"《周易研究》\s*((?:19|20)\d{2})\s*年\s*第\s*(\d{1,2})\s*期", label)
        if match and re.fullmatch(r"\.\./info/1033/\d+\.htm", href):
            return int(match[1]), str(int(match[2])), urljoin(url, href)
    raise ValueError("sdu_issue_link_missing")


def _sdu_toc(
    html: str, year: int, issue: str, url: str, observed: str
) -> tuple[str, list[PublisherArticle]]:
    if not re.search(rf"《周易研究》\s*{year}\s*年\s*第\s*{issue}\s*期", html):
        raise ValueError("sdu_issue_heading_mismatch")
    date_match = re.search(r"日期：\s*((?:19|20)\d{2}-\d{2}-\d{2})", html)
    if date_match is None:
        raise ValueError("sdu_announcement_date_missing")
    content = html.split('id="vsb_content"', 1)
    if len(content) != 2:
        raise ValueError("sdu_toc_missing")
    articles: list[PublisherArticle] = []
    sections = re.findall(
        r'<section\s+style="text-align:\s*center;\s*line-height:\s*2[^>]*>(.*?)</section>',
        content[1], re.S,
    )
    for section in sections:
        paragraphs = [_text(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", section, re.S)]
        paragraphs = [p for p in paragraphs if p]
        if len(paragraphs) < 2 or not 5 <= len(paragraphs[0]) <= 250:
            continue
        articles.append(PublisherArticle(
            "zhouyi-yanjiu", year, issue, paragraphs[0], paragraphs[1], url, url, observed,
        ))
    if not articles or len(articles) > MAX_ARTICLES:
        raise ValueError("sdu_articles_missing_or_excessive")
    return date_match[1], articles


def _cbpt_toc(
    html: str, url: str, observed: str, journal_id: str, host: str
) -> tuple[int, str, list[PublisherArticle]]:
    match = re.search(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*期", html)
    if match is None:
        raise ValueError("cbpt_issue_heading_missing")
    year, issue = int(match[1]), str(int(match[2]))
    body = html
    pattern = re.compile(
        rf'<h3[^>]*>\s*<a\s+href="(https://{re.escape(host)}/portal/journal/'
        r'portal/client/paper/[a-f0-9]{32})"[^>]*>(.*?)</a>\s*</h3>\s*<span[^>]*>(.*?)</span>',
        re.S,
    )
    articles = []
    for paper_url, title_html, author_html in pattern.findall(body):
        title = _text(title_html)
        author = _text(author_html).rstrip(";；") or None
        if author and 4 <= len(title) <= 250:
            articles.append(PublisherArticle(
                journal_id, year, issue, title, author, paper_url, url, observed
            ))
    if not articles or len(articles) > MAX_ARTICLES:
        raise ValueError("cbpt_articles_missing_or_excessive")
    return year, issue, articles


def _cbpt_page(html: str, host: str, year: int, issue: str, page: int) -> str | None:
    """Accept a same-host next-page link for the same issue, not arbitrary links."""
    for _, href in _links(html):
        parsed = urlsplit(href)
        if parsed.scheme != "https" or parsed.hostname != host:
            continue
        if parsed.path != "/portal/journal/portal/client/paperPage_list":
            continue
        params = parse_qs(parsed.query)
        if (params.get("pageNum") == [str(page)]
            and params.get("year") == [str(year)]
            and params.get("issue") == [f"{int(issue):02d}"]):
            return href
    return None


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS publisher_articles (
            journal_id TEXT NOT NULL, year INTEGER NOT NULL, issue TEXT NOT NULL,
            title TEXT NOT NULL, author TEXT, url TEXT NOT NULL, issue_url TEXT NOT NULL,
            first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,
            PRIMARY KEY (journal_id, year, issue, title)
        );
        CREATE TABLE IF NOT EXISTS publisher_article_checks (
            journal_id TEXT NOT NULL, year INTEGER NOT NULL, issue TEXT NOT NULL,
            title TEXT NOT NULL, source TEXT NOT NULL, checked_at TEXT NOT NULL,
            status TEXT NOT NULL, matched_url TEXT, reason TEXT,
            PRIMARY KEY (journal_id, year, issue, title, source, checked_at)
        );
        CREATE TABLE IF NOT EXISTS publisher_article_reviews (
            journal_id TEXT NOT NULL, year INTEGER NOT NULL, issue TEXT NOT NULL,
            title TEXT NOT NULL, article_url TEXT NOT NULL, basis_url TEXT NOT NULL,
            work_type TEXT NOT NULL, interest TEXT NOT NULL, reviewed_at TEXT NOT NULL,
            PRIMARY KEY (journal_id, year, issue, title)
        );
        CREATE TABLE IF NOT EXISTS publisher_query_scopes (
            journal_id TEXT NOT NULL, year INTEGER NOT NULL, issue TEXT NOT NULL,
            title TEXT NOT NULL, source TEXT NOT NULL, checked_at TEXT NOT NULL,
            query_kind TEXT NOT NULL, year_filter INTEGER,
            fetched_pages INTEGER NOT NULL, reported_pages INTEGER,
            complete INTEGER NOT NULL,
            PRIMARY KEY (journal_id, year, issue, title, source, checked_at)
        );
        CREATE TABLE IF NOT EXISTS publisher_issue_scans (
            journal_id TEXT NOT NULL, year INTEGER NOT NULL, issue TEXT NOT NULL,
            issue_url TEXT NOT NULL, first_completed_at TEXT NOT NULL,
            last_completed_at TEXT NOT NULL, pages INTEGER NOT NULL,
            article_count INTEGER NOT NULL, title_digest TEXT NOT NULL,
            PRIMARY KEY (journal_id, year, issue)
        );
        """)
        with db:
            yield db
    finally:
        db.close()


def _save_articles(db: sqlite3.Connection, articles: list[PublisherArticle], observed: str) -> None:
    for article in articles:
        db.execute("""INSERT INTO publisher_articles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(journal_id, year, issue, title) DO UPDATE SET
                author=excluded.author, url=excluded.url, issue_url=excluded.issue_url,
                last_observed_at=excluded.last_observed_at""",
            (*asdict(article).values(), observed),
        )


def _title_digest(titles: list[str]) -> str:
    return sha256("\n".join(sorted(titles)).encode("utf-8")).hexdigest()


def collect_publisher(
    journal_id: str, db_path: Path, *, catalog_path: Path = CATALOG_PATH,
    client: httpx.Client | None = None, max_pages: int = 3,
    observed_at: str | None = None,
) -> dict:
    if journal_id not in SUPPORTED or not 1 <= max_pages <= 3:
        raise ValueError("unsupported journal or page bound")
    journal = next(j for j in load_catalog(catalog_path) if j.id == journal_id)
    host = urlsplit(journal.official_url).hostname
    assert host is not None
    observed = observed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    active = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        html = _fetch(active, journal.official_url, host)
        pdf_pages = None
        secondary_announcement = None
        if journal_id == "zhouyi-yanjiu":
            year, issue, issue_url = _sdu_index(html, journal.official_url)
            issue_html = _fetch(active, issue_url, host)
            announced, articles = _sdu_toc(issue_html, year, issue, issue_url, observed)
            status = "pending"  # A parsed announcement day still needs human source review.
            label_month = None
            pages = 2
        elif journal_id == "kongzi-yanjiu":
            if max_pages < 2:
                raise ValueError("kongzi_requires_two_request_pages")
            year, issue, issue_url = _kongzi_index(html, journal.official_url)
            issue_html = _fetch(active, issue_url, host)
            announced, articles = _kongzi_toc(issue_html, year, issue, issue_url, observed)
            label_month = None
            pages = 2
            status = "pending"
        elif journal_id == "xiandai-zhexue":
            if max_pages < 3:
                raise ValueError("sysu_requires_three_request_pages")
            year, issue, page_url = _sysu_index(html, journal.official_url)
            page_html = _fetch(active, page_url, host)
            page_announcement, issue_url = _sysu_article_page(page_html, page_url, host)
            pdf_content = _fetch_pdf(active, issue_url, host)
            pdf_text, pdf_pages = _extract_pdf_text(pdf_content)
            label_month, articles = _sysu_toc(pdf_text, year, issue, issue_url, observed)
            announced = None
            secondary_announcement = (page_url, page_announcement)
            pages = 3
            status = "pending"
        elif journal_id in CBPT_IDS:
            issue_url = f"https://{host}/portal/journal/portal/client/paper_list/type_benqi"
            page_html = _fetch(active, issue_url, host)
            year, issue, articles = _cbpt_toc(
                page_html, issue_url, observed, journal_id, host
            )
            pages = 2
            for page in range(2, max_pages + 1):
                next_url = _cbpt_page(page_html, host, year, issue, page)
                if next_url is None:
                    break
                page_html = _fetch(active, next_url, host)
                other_year, other_issue, batch = _cbpt_toc(
                    page_html, issue_url, observed, journal_id, host
                )
                if (other_year, other_issue) != (year, issue):
                    raise ValueError("cbpt_pagination_issue_changed")
                articles.extend(batch)
                pages += 1
            if _cbpt_page(page_html, host, year, issue, max_pages + 1):
                raise ValueError("cbpt_toc_truncated_by_page_bound")
            announced = None
            label_month = None
            status = "pending"
        else:
            issue, articles = _jdn_toc(html, journal.official_url, observed)
            year = articles[0].year
            issue_url = journal.official_url
            pages = 1
            for page in range(2, max_pages + 1):
                page_url = f"{journal.official_url}?page={page}"
                page_html = _fetch(active, page_url, host)
                other_issue, batch = _jdn_toc(page_html, page_url, observed)
                if other_issue != issue:
                    raise ValueError("jdn_pagination_issue_changed")
                articles.extend(batch)
                pages += 1
                next_href = f"/home/journal/cataloglist/cid/349?page={page + 1}"
                if not any(href == next_href for _, href in _links(page_html)):
                    break
            if any(
                href == f"/home/journal/cataloglist/cid/349?page={max_pages + 1}"
                for _, href in _links(page_html if max_pages > 1 else html)
            ):
                raise ValueError("jdn_toc_truncated_by_page_bound")
            if len(articles) > MAX_ARTICLES:
                raise ValueError("jdn_articles_exceed_bound")
            announced = None
            label_month = None
            status = "pending"  # An issue number is not an explicitly dated month.
        unique = list({article.title: article for article in articles}.values())
        if len(unique) != len(articles):
            raise ValueError("publisher_duplicate_titles")
        with _connect(db_path) as db:
            _save_articles(db, unique, observed)
            db.execute(
                """INSERT INTO publisher_issue_scans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(journal_id, year, issue) DO UPDATE SET
                    issue_url=excluded.issue_url,
                    last_completed_at=excluded.last_completed_at,
                    pages=excluded.pages,
                    article_count=excluded.article_count,
                    title_digest=excluded.title_digest""",
                (journal_id, year, issue, issue_url, observed, observed, pages,
                 len(unique), _title_digest([article.title for article in unique])),
            )
        record_issue(db_path, OfficialIssue(
            journal_id, journal.title, year, issue, issue_url,
            "publisher-site" if journal.channel == "publisher-institute" else "journal-site",
            label_month, None, announced, observed, status, None,
        ), catalog_path=catalog_path)
        if secondary_announcement is not None:
            page_url, page_announcement = secondary_announcement
            record_issue(db_path, OfficialIssue(
                journal_id, journal.title, year, issue, page_url,
                "publisher-site", None, None, page_announcement, observed, "pending", None,
            ), catalog_path=catalog_path)
        saved = next(
            item for item in list_issues(db_path)
            if (item.journal_id, item.year, item.issue, item.evidence_url)
            == (journal_id, year, issue, issue_url)
        )
        result = {"journal": journal_id, "year": year, "issue": issue,
                  "articles": len(unique), "pages": pages,
                  "issue_status": saved.status, "issue_url": issue_url}
        if pdf_pages is not None:
            result["pdf_pages"] = pdf_pages
        return result
    finally:
        if client is None:
            active.close()


def _first_publisher_author(raw: str | None) -> str | None:
    if not raw:
        return None
    first = re.split(r"[;；、,，]+", raw, maxsplit=1)[0].strip()
    parts = first.split()
    if 1 < len(parts) <= 3 and all(re.fullmatch(r"[\u3400-\u9fff]+", part) for part in parts):
        if sum(map(len, parts)) <= 4 and any(len(part) == 1 for part in parts):
            return "".join(parts)
        return parts[0]
    return first


def _identity(article: sqlite3.Row, title: str, authors: tuple[str, ...], venue: str | None,
              year: int | None, issue: str | None, official_venue: str) -> str:
    expected_author = _first_publisher_author(article["author"])
    decision = compare_bibliographic_identity(article["title"], expected_author, title, authors)
    if decision.level == IdentityMatchLevel.DISTINCT:
        return "distinct"
    if decision.level == IdentityMatchLevel.REVIEW_REQUIRED:
        return "review"
    if venue and "".join(venue.split()).strip("《》") != official_venue:
        return "review"
    if year is not None and year != article["year"]:
        return "review"
    if issue is not None and issue.lstrip("0") != article["issue"].lstrip("0"):
        return "review"
    return "hit" if authors and article["author"] and venue and year and issue else "review"


def _cnki_check(
    client: httpx.Client, article: sqlite3.Row, venue: str, scope: dict | None = None,
) -> tuple[str, str | None, str | None]:
    if scope is not None:
        scope.update(query_kind="title_year", year_filter=article["year"],
                     fetched_pages=0, reported_pages=None, complete=False)
    try:
        records, pages = fetch_result_page(client, CnkiSearchTerm(article["title"]),
                                           year=article["year"], page=1)
        if scope is not None:
            scope.update(fetched_pages=1, reported_pages=pages)
        if pages > 2:
            return "incomplete", None, "page_bound"
        if pages == 2:
            more, second_pages = fetch_result_page(
                client, CnkiSearchTerm(article["title"]), year=article["year"], page=2
            )
            if scope is not None:
                scope["fetched_pages"] = 2
            if second_pages != pages:
                return "incomplete", None, "page_count_changed"
            records += more
    except CnkiSpaceError as error:
        return "failed", None, str(error)
    if scope is not None:
        scope["complete"] = True
    decisions = [(_identity(article, item.title, item.authors, item.venue, item.year,
                            item.issue, venue), item.url) for item in records]
    for desired in ("hit", "review"):
        for status, url in decisions:
            if status == desired:
                return status, url, None
    return "miss", None, None


def _wanfang_check(client: httpx.Client, article: sqlite3.Row, venue: str,
                   key: str, scope: dict | None = None) -> tuple[str, str | None, str | None]:
    records = ()
    if scope is not None:
        scope.update(query_kind="title", year_filter=None, fetched_pages=0,
                     reported_pages=None, complete=False)
    try:
        for start in (0, PAGE_ROWS):
            batch, total = fetch_query_page(client, article["title"], key, start=start)
            if scope is not None:
                scope["fetched_pages"] += 1
                scope["reported_pages"] = max(1, (total + PAGE_ROWS - 1) // PAGE_ROWS)
            records += batch
            if start + PAGE_ROWS >= total:
                break
            if total > PAGE_ROWS * 2:
                return "incomplete", None, "page_bound"
    except WanfangError as error:
        return "failed", None, str(error)
    if scope is not None:
        scope["complete"] = True
    decisions = [(_identity(article, item.title, item.authors, item.venue, item.year,
                            item.issue, venue), item.record_id) for item in records]
    for desired in ("hit", "review"):
        for status, record_id in decisions:
            if status == desired:
                url = f"https://www.wanfangdata.com.cn/details/detail.do?_type=perio&id={record_id}"
                return status, url, None
    return "miss", None, None


def check_publisher_articles(
    journal_id: str, db_path: Path, *, catalog_path: Path = CATALOG_PATH,
    max_checks: int = 5, min_interval_hours: int = 24,
    with_wanfang: bool = False, key_file: Path | None = None, client: httpx.Client | None = None,
    checked_at: str | None = None,
) -> dict:
    if journal_id not in SUPPORTED or not 1 <= max_checks <= MAX_CHECKS or min_interval_hours < 0:
        raise ValueError("invalid check bounds")
    venue = next(j.title for j in load_catalog(catalog_path) if j.id == journal_id)
    now = checked_at or datetime.now().astimezone().isoformat(timespec="seconds")
    key, key_error = _read_key(None, key_file) if with_wanfang else ("", None)
    active = client or httpx.Client(timeout=20, follow_redirects=False)
    results: list[dict] = []
    try:
        with _connect(db_path) as db:
            rows = db.execute("""SELECT a.*, c.last_checked FROM publisher_articles a
                LEFT JOIN (SELECT journal_id, year, issue, title,
                    MAX(checked_at) AS last_checked FROM publisher_article_checks
                    GROUP BY journal_id, year, issue, title) c
                USING (journal_id, year, issue, title)
                WHERE a.journal_id=? ORDER BY c.last_checked IS NOT NULL,
                    c.last_checked, a.year DESC, CAST(a.issue AS INTEGER) DESC, a.title
                LIMIT ?""", (journal_id, max_checks * 3)).fetchall()
            eligible = []
            for row in rows:
                last = row["last_checked"]
                elapsed = (
                    datetime.fromisoformat(now) - datetime.fromisoformat(last)
                ).total_seconds() if last is not None else None
                if elapsed is not None and elapsed < min_interval_hours * 3600:
                    continue
                eligible.append(row)
                if len(eligible) == max_checks:
                    break
            for row in eligible:
                for source in (("cnki", "wanfang") if with_wanfang else ("cnki",)):
                    scope: dict = {}
                    if source == "wanfang" and key_error:
                        status, url, reason = "unavailable", None, key_error
                    else:
                        status, url, reason = (
                            _cnki_check(active, row, venue, scope) if source == "cnki"
                            else _wanfang_check(active, row, venue, key, scope)
                        )
                    db.execute("""INSERT OR REPLACE INTO publisher_article_checks
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (row["journal_id"], row["year"], row["issue"], row["title"],
                         source, now, status, url, reason))
                    if scope:
                        db.execute("""INSERT OR REPLACE INTO publisher_query_scopes
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (row["journal_id"], row["year"], row["issue"], row["title"],
                             source, now, scope["query_kind"], scope["year_filter"],
                             scope["fetched_pages"], scope["reported_pages"],
                             int(scope["complete"])))
                    results.append({"title": row["title"], "source": source,
                                    "status": status, "matched_url": url, "reason": reason})
            db.commit()
    finally:
        if client is None:
            active.close()
    return {"journal": journal_id, "checked_articles": len(eligible), "checks": results}


def _cycle_allocations(
    journal_ids: tuple[str, ...], total: int, per_site: int, ordinal: int
) -> dict[str, int]:
    if not journal_ids or not 0 <= total <= 30 or not 1 <= per_site <= MAX_CHECKS:
        raise ValueError("invalid cycle bounds")
    rotated = journal_ids[ordinal % len(journal_ids):] + journal_ids[:ordinal % len(journal_ids)]
    allocation = dict.fromkeys(journal_ids, 0)
    for _ in range(total):
        available = [journal for journal in rotated if allocation[journal] < per_site]
        if not available:
            break
        allocation[min(available, key=lambda journal: allocation[journal])] += 1
    return allocation


def run_publisher_cycle(
    db_path: Path, *, catalog_path: Path = CATALOG_PATH,
    total_checks: int = 0, checks_per_site: int = 2, min_interval_hours: int = 24,
    with_wanfang: bool = False, key_file: Path | None = None,
) -> dict:
    """Scan every supported site; distribute a bounded article-check budget."""
    journals = tuple(sorted(SUPPORTED))
    allocation = _cycle_allocations(
        journals, total_checks, checks_per_site,
        datetime.now().astimezone().date().toordinal(),
    )
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    results = []
    for journal_id in journals:
        item: dict = {"journal": journal_id, "scan": None, "check": None, "failure": None}
        try:
            item["scan"] = collect_publisher(journal_id, db_path, catalog_path=catalog_path)
        except (ValueError, OSError, sqlite3.Error) as error:
            item["failure"] = str(error) if isinstance(error, ValueError) else type(error).__name__
        if allocation[journal_id]:
            try:
                item["check"] = check_publisher_articles(
                    journal_id, db_path, catalog_path=catalog_path,
                    max_checks=allocation[journal_id],
                    min_interval_hours=min_interval_hours, with_wanfang=with_wanfang,
                    key_file=key_file,
                )
            except (ValueError, OSError, sqlite3.Error) as error:
                item["failure"] = (
                    str(error) if isinstance(error, ValueError) else type(error).__name__
                )
        results.append(item)
    return {
        "started_at": started,
        "sites_attempted": len(journals),
        "sites_failed": sum(item["failure"] is not None for item in results),
        "article_check_budget": total_checks,
        "articles_checked": sum(
            item["check"]["checked_articles"] for item in results if item["check"]
        ),
        "sites": results,
    }


def observation_summary(db_path: Path, journal_id: str | None = None) -> list[dict]:
    """Report interval-censored first hits; a missing hit is only right-censored."""
    if not db_path.is_file():
        return []
    with _connect(db_path) as db:
        rows = db.execute("""SELECT a.journal_id, a.year, a.issue, a.title,
            a.first_observed_at, c.source, c.checked_at, c.status, s.complete
            FROM publisher_articles a JOIN publisher_article_checks c
            USING (journal_id, year, issue, title)
            LEFT JOIN publisher_query_scopes s USING
            (journal_id, year, issue, title, source, checked_at)
            WHERE (? IS NULL OR a.journal_id=?)
            ORDER BY a.journal_id, a.year DESC, a.issue DESC, a.title, c.source, c.checked_at""",
            (journal_id, journal_id)).fetchall()
    grouped: dict[tuple, dict] = {}
    for row in rows:
        key = (row["journal_id"], row["year"], row["issue"], row["title"], row["source"])
        item = grouped.setdefault(key, {"journal": row["journal_id"], "year": row["year"],
            "issue": row["issue"], "title": row["title"], "source": row["source"],
            "official_first_seen": row["first_observed_at"], "last_complete_miss": None,
            "first_hit": None, "latest_status": None})
        item["latest_status"] = row["status"]
        if row["status"] == "miss" and row["complete"] == 1 and item["first_hit"] is None:
            item["last_complete_miss"] = row["checked_at"]
        elif row["status"] == "hit" and item["first_hit"] is None:
            item["first_hit"] = row["checked_at"]
    for item in grouped.values():
        if item["first_hit"] and item["last_complete_miss"]:
            item["arrival_evidence"] = "interval_censored"
        elif item["first_hit"]:
            item["arrival_evidence"] = "left_censored"
        elif item["last_complete_miss"]:
            item["arrival_evidence"] = "right_censored_on_search_surface"
        else:
            item["arrival_evidence"] = "unknown"
    return list(grouped.values())


def measurement_summary(db_path: Path, journal_id: str | None = None) -> list[dict]:
    """Count observed hits and non-hits, without estimating inaccessible coverage."""
    observations = observation_summary(db_path, journal_id)
    result = []
    for source in ("cnki", "wanfang"):
        rows = [item for item in observations if item["source"] == source]
        result.append({
            "source": source,
            "observed_articles": len(rows),
            "ever_hit": sum(item["first_hit"] is not None for item in rows),
            "searched_without_hit": sum(
                item["first_hit"] is None and item["last_complete_miss"] is not None
                for item in rows
            ),
            "arrival_intervals": sum(
                item["arrival_evidence"] == "interval_censored" for item in rows
            ),
            "unknown_or_failed": sum(item["arrival_evidence"] == "unknown" for item in rows),
        })
    return result


def review_publisher_article(
    db_path: Path, journal_id: str, year: int, issue: str, title: str,
    article_url: str, *, work_type: str, interest: str,
    basis_url: str | None = None, catalog_path: Path = CATALOG_PATH,
) -> dict:
    """Store a local human decision, never infer it from a journal or keyword."""
    if work_type not in {"research", "other", "uncertain"}:
        raise ValueError("invalid article work type")
    if interest not in {"relevant", "irrelevant", "uncertain", "unreviewed"}:
        raise ValueError("invalid article interest decision")
    if work_type != "research" and interest != "unreviewed":
        raise ValueError("interest decision requires a reviewed research article")
    if not db_path.is_file():
        raise ValueError("no collected publisher article exists")
    journal = next((item for item in load_catalog(catalog_path) if item.id == journal_id), None)
    if journal is None:
        raise ValueError("unknown official journal")
    basis = basis_url or article_url
    parsed = urlsplit(basis)
    if (parsed.scheme != "https" or parsed.hostname != urlsplit(journal.official_url).hostname
        or parsed.port not in (None, 443)):
        raise ValueError("article review basis must be on the registered publisher host")
    reviewed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with _connect(db_path) as db:
        row = db.execute(
            """SELECT url FROM publisher_articles WHERE journal_id=? AND year=?
            AND issue=? AND title=?""", (journal_id, year, issue, title)
        ).fetchone()
        if row is None or row["url"] != article_url:
            raise ValueError("article review requires an exact collected article and URL")
        db.execute(
            """INSERT INTO publisher_article_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(journal_id, year, issue, title) DO UPDATE SET
                article_url=excluded.article_url, basis_url=excluded.basis_url,
                work_type=excluded.work_type, interest=excluded.interest,
                reviewed_at=excluded.reviewed_at""",
            (journal_id, year, issue, title, article_url, basis,
             work_type, interest, reviewed_at),
        )
    return {"journal": journal_id, "year": year, "issue": issue, "title": title,
            "article_url": article_url, "basis_url": basis, "work_type": work_type,
            "interest": interest, "reviewed_at": reviewed_at}


def issue_coverage_summary(
    db_path: Path, *, journal_id: str | None = None, year: int | None = None,
    issue: str | None = None, include_articles: bool = False,
) -> list[dict]:
    """Use only complete publisher TOCs as a reference denominator."""
    if not db_path.is_file():
        return []
    with _connect(db_path) as db:
        articles = db.execute(
            """SELECT * FROM publisher_articles WHERE (? IS NULL OR journal_id=?)
            AND (? IS NULL OR year=?) AND (? IS NULL OR issue=?)
            ORDER BY journal_id, year DESC, CAST(issue AS INTEGER) DESC, title""",
            (journal_id, journal_id, year, year, issue, issue),
        ).fetchall()
        scans = {
            (row["journal_id"], row["year"], row["issue"]): row
            for row in db.execute("SELECT * FROM publisher_issue_scans").fetchall()
        }
        reviews = {
            (row["journal_id"], row["year"], row["issue"], row["title"]): row
            for row in db.execute("SELECT * FROM publisher_article_reviews").fetchall()
        }
        latest_checks = {}
        for row in db.execute(
            """SELECT c.*, s.query_kind, s.year_filter, s.fetched_pages,
            s.reported_pages, s.complete FROM publisher_article_checks c
            LEFT JOIN publisher_query_scopes s USING
            (journal_id, year, issue, title, source, checked_at)
            ORDER BY c.checked_at"""
        ).fetchall():
            latest_checks[(row["journal_id"], row["year"], row["issue"],
                           row["title"], row["source"])] = row
    grouped: dict[tuple[str, int, str], list[sqlite3.Row]] = {}
    for article in articles:
        grouped.setdefault((article["journal_id"], article["year"], article["issue"]),
                           []).append(article)
    results = []
    for key, rows in grouped.items():
        scan = scans.get(key)
        complete = bool(
            scan and scan["article_count"] == len(rows)
            and scan["title_digest"] == _title_digest([row["title"] for row in rows])
        )
        types = dict.fromkeys(("research", "other", "uncertain", "unreviewed"), 0)
        interests = dict.fromkeys(("relevant", "irrelevant", "uncertain", "unreviewed"), 0)
        sources = {
            source: {"hit": 0, "miss": 0, "review": 0, "failed": 0,
                     "incomplete": 0, "unavailable": 0, "unchecked": 0,
                     "complete_queries": 0, "complete_misses": 0, "scope_missing": 0}
            for source in ("cnki", "wanfang")
        }
        research_sources = {
            source: {"complete_hits": 0, "complete_misses": 0,
                     "unresolved": 0, "visibility_denominator": None}
            for source in ("cnki", "wanfang")
        }
        details = []
        for row in rows:
            article_key = (*key, row["title"])
            review = reviews.get(article_key)
            work_type = review["work_type"] if review else "unreviewed"
            interest = review["interest"] if review and work_type == "research" else "unreviewed"
            types[work_type] += 1
            if work_type == "research":
                interests[interest] += 1
            checks = {}
            for source, counts in sources.items():
                check = latest_checks.get((*article_key, source))
                status = check["status"] if check else "unchecked"
                counts[status] += 1
                if check and check["complete"] == 1:
                    counts["complete_queries"] += 1
                    if status == "miss":
                        counts["complete_misses"] += 1
                elif check and check["query_kind"] is None:
                    counts["scope_missing"] += 1
                if work_type == "research":
                    research_counts = research_sources[source]
                    if check and check["complete"] == 1 and status == "hit":
                        research_counts["complete_hits"] += 1
                    elif check and check["complete"] == 1 and status == "miss":
                        research_counts["complete_misses"] += 1
                    else:
                        research_counts["unresolved"] += 1
                checks[source] = (
                    {"status": status, "checked_at": check["checked_at"],
                     "reason": check["reason"], "query_kind": check["query_kind"],
                     "year_filter": check["year_filter"],
                     "fetched_pages": check["fetched_pages"],
                     "reported_pages": check["reported_pages"],
                     "complete": bool(check["complete"]) if check["complete"] is not None
                     else None}
                    if check else {"status": status}
                )
            if include_articles:
                details.append({"title": row["title"], "author": row["author"],
                                "url": row["url"], "work_type": work_type,
                                "interest": interest, "checks": checks})
        research_denominator = (
            types["research"] if complete and not types["unreviewed"]
            and not types["uncertain"] else None
        )
        if research_denominator is not None:
            for counts in research_sources.values():
                if not counts["unresolved"]:
                    counts["visibility_denominator"] = research_denominator
        result = {"journal": key[0], "year": key[1], "issue": key[2],
                  "issue_url": scan["issue_url"] if scan else rows[0]["issue_url"],
                  "publisher_records": len(rows), "toc_complete": complete,
                  "reference_denominator": len(rows) if complete else None,
                  "research_denominator": research_denominator,
                  "interest_denominator": (
                      interests["relevant"] if complete and not types["unreviewed"]
                      and not types["uncertain"] and not interests["unreviewed"]
                      and not interests["uncertain"] else None
                  ),
                  "last_completed_at": scan["last_completed_at"] if scan else None,
                  "work_types": types, "research_interest": interests,
                  "source_checks": sources,
                  "research_source_checks": research_sources}
        if include_articles:
            result["articles"] = details
        results.append(result)
    return results
