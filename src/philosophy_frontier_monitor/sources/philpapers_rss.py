"""PhilPapers category-page RSS discovery and parsing.

PhilPapers exposes the RSS action through JavaScript. The page's ``allparams``
form is the source of truth; this module reproduces only the documented page
interaction and never guesses category IDs from slugs.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse
from xml.etree import ElementTree

import httpx

from ..http_retry import BoundedRequestError, request_with_retry

ALLOWED_HOSTS = frozenset({"philpapers.org", "www.philpapers.org"})
DEFAULT_USER_AGENT = "PhilosophyFrontierMonitor/0.1"


class PhilPapersFeedError(RuntimeError):
    """Raised when category feed discovery or parsing cannot be verified."""


@dataclass(frozen=True, slots=True)
class FeedRequest:
    category_page_url: str
    feed_url: str
    method: str
    category_id: str
    category_slug: str
    parameters: dict[str, str]


@dataclass(frozen=True, slots=True)
class FeedEntry:
    source_id: str
    title: str
    link: str
    description: str | None
    published_text: str | None


@dataclass(frozen=True, slots=True)
class DisplayBibliography:
    title: str
    author_text: str | None


class _AllParamsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_target_form = False
        self.action: str | None = None
        self.method = "GET"
        self.parameters: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.casefold() == "form" and attributes.get("id") == "allparams":
            self.in_target_form = True
            self.action = attributes.get("action")
            self.method = (attributes.get("method") or "GET").upper()
            return
        if self.in_target_form and tag.casefold() == "input":
            name = attributes.get("name")
            if name:
                self.parameters[name] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "form" and self.in_target_form:
            self.in_target_form = False


class _GeneratedFeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a" or self.href is not None:
            return
        href = dict(attrs).get("href")
        if not href:
            return
        query = parse_qs(urlparse(href).query)
        if query.get("format") == ["rss"] and query.get("dg"):
            self.href = href


def _validate_philpapers_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise PhilPapersFeedError("only HTTPS PhilPapers URLs are allowed")


def parse_feed_request(html: str, category_page_url: str) -> FeedRequest:
    """Reconstruct the RSS request produced by PhilPapers' page control."""

    _validate_philpapers_url(category_page_url)
    parser = _AllParamsParser()
    parser.feed(html)
    if parser.action is None:
        raise PhilPapersFeedError("category page has no allparams form")

    category_id = parser.parameters.get("cId") or parser.parameters.get("catId")
    category_slug = parser.parameters.get("cn")
    if not category_id or not category_id.isdecimal():
        raise PhilPapersFeedError("category page has no numeric cId")
    if not category_slug:
        raise PhilPapersFeedError("category page has no category slug parameter")

    # The page's RSS JavaScript renames two empty helper inputs before submit:
    # ap_c1 -> noheader=1 and ap_c2 -> __action=<original form action>.
    parameters = dict(parser.parameters)
    parameters.pop("ap_c1", None)
    parameters.pop("ap_c2", None)
    parameters["noheader"] = "1"
    parameters["__action"] = parser.action
    parameters["format"] = ""
    feed_url = urljoin(category_page_url, "/utils/feed.pl")
    _validate_philpapers_url(feed_url)
    return FeedRequest(
        category_page_url=category_page_url,
        feed_url=feed_url,
        method=parser.method,
        category_id=category_id,
        category_slug=category_slug,
        parameters=parameters,
    )


def discover_feed_request(
    category_page_url: str,
    *,
    client: httpx.Client | None = None,
) -> FeedRequest:
    """Fetch a category page and parse its current feed-generation parameters."""

    _validate_philpapers_url(category_page_url)
    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=30
    )
    try:
        response = request_with_retry(
            lambda: active_client.get(category_page_url),
            source="PhilPapers",
        )
        _validate_philpapers_url(str(response.url))
        return parse_feed_request(response.text, str(response.url))
    except BoundedRequestError as error:
        raise PhilPapersFeedError(f"failed to fetch category page: {error}") from error
    finally:
        if owns_client:
            active_client.close()


def fetch_feed(
    request: FeedRequest,
    *,
    client: httpx.Client | None = None,
) -> str:
    """Submit a previously discovered feed request and return verified XML text."""

    _validate_philpapers_url(request.feed_url)
    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=30
    )
    try:
        if request.method == "POST":
            response = request_with_retry(
                lambda: active_client.post(request.feed_url, data=request.parameters),
                source="PhilPapers",
            )
        else:
            response = request_with_retry(
                lambda: active_client.get(request.feed_url, params=request.parameters),
                source="PhilPapers",
            )
        _validate_philpapers_url(str(response.url))

        content_type = response.headers.get("content-type", "").casefold()
        if "html" in content_type:
            # /utils/feed.pl first returns a page containing the generated,
            # tokenized RSS link. This is the same link presented to a user.
            link_parser = _GeneratedFeedLinkParser()
            link_parser.feed(response.text)
            if link_parser.href is None:
                raise PhilPapersFeedError("feed-generation page has no tokenized RSS link")
            generated_url = urljoin(str(response.url), link_parser.href)
            _validate_philpapers_url(generated_url)
            response = request_with_retry(
                lambda: active_client.get(generated_url),
                source="PhilPapers",
            )
            _validate_philpapers_url(str(response.url))
            content_type = response.headers.get("content-type", "").casefold()

        if not any(kind in content_type for kind in ("xml", "rss", "atom")):
            raise PhilPapersFeedError(f"unexpected feed content type: {content_type or 'missing'}")
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as error:
            raise PhilPapersFeedError("feed response is not valid XML") from error
        if root.tag.casefold().split("}")[-1] not in {"rss", "feed", "rdf"}:
            raise PhilPapersFeedError(f"unexpected feed root element: {root.tag}")
        return response.text
    except BoundedRequestError as error:
        raise PhilPapersFeedError(f"failed to fetch category feed: {error}") from error
    finally:
        if owns_client:
            active_client.close()


def _child_text(element: ElementTree.Element, local_name: str) -> str | None:
    for child in element:
        if child.tag.split("}")[-1].casefold() == local_name.casefold() and child.text:
            return child.text.strip()
    return None


def parse_feed(xml_text: str) -> tuple[FeedEntry, ...]:
    """Parse RSS 2.0 or Atom entries without treating feed dates as publication proof."""

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise PhilPapersFeedError("feed response is not valid XML") from error

    entries: list[FeedEntry] = []
    for element in root.iter():
        local_tag = element.tag.split("}")[-1].casefold()
        if local_tag not in {"item", "entry"}:
            continue
        title = _child_text(element, "title")
        link = _child_text(element, "link")
        if link is None:
            for child in element:
                if child.tag.split("}")[-1].casefold() == "link" and child.get("href"):
                    link = child.get("href")
                    break
        source_id = _child_text(element, "guid") or _child_text(element, "id") or link
        if not title or not link or not source_id:
            continue
        entries.append(
            FeedEntry(
                source_id=source_id,
                title=title,
                link=link,
                description=_child_text(element, "description") or _child_text(element, "summary"),
                published_text=_child_text(element, "pubDate")
                or _child_text(element, "published")
                or _child_text(element, "updated"),
            )
        )
    return tuple(entries)


def split_display_bibliography(feed_title: str) -> DisplayBibliography:
    """Split PhilPapers' common ``Surname, Given: Title`` display form.

    The split is only a query aid. It is not accepted as an author identity
    assertion until another source confirms the bibliography.
    """

    author_text, separator, title = feed_title.partition(": ")
    if separator and "," in author_text and title.strip():
        return DisplayBibliography(title=title.strip(), author_text=author_text.strip())
    return DisplayBibliography(title=feed_title.strip(), author_text=None)
