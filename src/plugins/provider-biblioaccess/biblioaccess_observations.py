"""Offline, non-authoritative BiblioAccess route and ACSM observations.

Pure helpers do no HTTP, session import, lending or fulfillment. They never
retain authorization-bearing query values. The caller must independently
validate provenance, response integrity and Core-owned identity scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urlencode, urljoin, urlsplit
from xml.etree import ElementTree as ET

BOOK_HOST = "www.biblioaccess.com"
TENANT = "1409"
MAX_HTML_BYTES = 2_000_000
MAX_ACSM_BYTES = 128_000
_BOOK_PATH = re.compile(r"^/1409/Catalog/Book/([0-9]{1,18})(?:/[^/?#]*)?/?$")
_SEARCH_PATH = "/1409/Catalog/Search"
_SUMMARY_PATH = "/1409/Catalog/SearchResultSummary"
_ALLOWED_SEARCH_PARAMS = frozenset({
    "query", "type", "pageindex", "format", "pubDateRange",
    "language", "subjectFilter",
})
_ADEPT = "http://ns.adobe.com/adept"


def _parts(url: str):
    if type(url) is not str or not url or url != url.strip():
        return None
    if any(ord(ch) < 32 or ch == "\\"
           or ord(ch) == 127 for ch in url):
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname != BOOK_HOST
                or parts.port not in (None, 443) or parts.username
                or parts.password or parts.fragment):
            return None
        return parts
    except ValueError:
        return None


def search_summary_url(query: str) -> str:
    """Only a bounded textual query, no untrusted URL or parameter keys."""
    if type(query) is not str or not query.strip() or len(query) > 512:
        raise ValueError("biblioaccess_invalid_search_query")
    return ("https://" + BOOK_HOST + _SUMMARY_PATH
            + "?" + urlencode({"query": query.strip()}))


def classify_search_response(*, request_url: str, status: int,
                             location: str = "") -> str:
    """HTTP statuses are not result counts or evidence of a complete listing."""
    parts = _parts(request_url)
    if parts is None or parts.path not in (_SEARCH_PATH, _SUMMARY_PATH):
        return "invalid"
    if status in (401, 403):
        return "authentication_required"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "remote_error"
    if status == 302 and parts.path == _SUMMARY_PATH:
        target = _parts(urljoin(request_url, location))
        return ("search_redirect" if target and target.path == _SEARCH_PATH
                else "untrusted_redirect")
    if status == 200:
        return "html_unparsed"
    return "unsupported"


def book_id_from_url(url: str) -> str | None:
    """Notice identifier only; never a loan ID or an entitlement."""
    parts = _parts(url)
    if parts is None or parts.query:
        return None
    match = _BOOK_PATH.fullmatch(parts.path)
    return match.group(1) if match else None


@dataclass(frozen=True)
class CatalogueLinks:
    book_ids: tuple[str, ...]
    next_page_url: str | None
    completeness: str = "partial"


class _Links(HTMLParser):
    def __init__(self, page_url: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.book_ids = []
        self.next_page_url = None
        self._candidate = None
        self._anchor_text = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        values = dict(attrs)
        href = values.get("href")
        if not isinstance(href, str) or not href.strip():
            self._candidate = None
            return
        link = urljoin(self.page_url, href)
        ident = book_id_from_url(link)
        if ident and ident not in self.book_ids and len(self.book_ids) < 300:
            self.book_ids.append(ident)
        parts = _parts(link)
        self._candidate = None
        if parts and parts.path == _SEARCH_PATH:
            from urllib.parse import parse_qs
            params = parse_qs(parts.query, keep_blank_values=True)
            if (params.keys() <= _ALLOWED_SEARCH_PARAMS
                    and "pageindex" in params
                    and len(params["pageindex"]) == 1
                    and re.fullmatch(r"[0-9]{1,5}", params["pageindex"][0])
                    and int(params["pageindex"][0]) <= 100):
                self._candidate = link
                self._anchor_text = []
                if "next" in str(values.get("rel") or "").lower().split():
                    self.next_page_url = link

    def handle_data(self, data):
        if self._candidate is not None:
            self._anchor_text.append(data[:100])

    def handle_endtag(self, tag):
        if tag == "a" and self._candidate is not None:
            label = " ".join(self._anchor_text).strip().casefold()
            if label.startswith(("suiv", "next")):
                self.next_page_url = self._candidate
            self._candidate = None
            self._anchor_text = []


def extract_catalogue_links(page_url: str, html: str) -> CatalogueLinks:
    """Conservative link extraction; missing links never prove zero results."""
    parts = _parts(page_url)
    if not parts or parts.path != _SEARCH_PATH:
        raise ValueError("biblioaccess_invalid_catalogue_page")
    if type(html) is not str or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("biblioaccess_invalid_html")
    parser = _Links(page_url)
    parser.feed(html)
    parser.close()
    return CatalogueLinks(tuple(parser.book_ids), parser.next_page_url)


@dataclass(frozen=True)
class AcsmStructure:
    kind: str
    drm: str
    format: str
    duration_display_seconds: int | None
    duration_play_seconds: int | None
    content_available: bool = False
    fulfillment_complete: bool = False


def inspect_acsm_structure(data: bytes) -> AcsmStructure:
    """Structural ADEPT validation only. Deliberately drop tokens and URLs.

    Does not infer borrowing due date, issuer identity, token validity,
    authentication, completed ADEPT fulfillment or EPUB presence.
    """
    if type(data) is not bytes or not 0 < len(data) <= MAX_ACSM_BYTES:
        raise ValueError("biblioaccess_invalid_acsm")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("biblioaccess_invalid_acsm")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ValueError("biblioaccess_invalid_acsm") from None
    if root.tag != "{" + _ADEPT + "}fulfillmentToken":
        raise ValueError("biblioaccess_not_adept")
    def duration(operation):
        nodes = root.findall(
            ".//{" + _ADEPT + "}permissions/{" + _ADEPT + "}" +
            operation + "/{" + _ADEPT + "}duration"
        )
        if len(nodes) != 1 or nodes[0].text is None:
            return None
        value = nodes[0].text.strip()
        if not re.fullmatch(r"[0-9]{1,10}", value):
            return None
        seconds = int(value)
        return seconds if 0 <= seconds <= 315_360_000 else None

    return AcsmStructure(
        kind="loan" if root.attrib.get("fulfillmentType") == "loan"
        else "unknown",
        drm="adobe-adept",
        format="acsm",
        duration_display_seconds=duration("display"),
        duration_play_seconds=duration("play"),
    )
