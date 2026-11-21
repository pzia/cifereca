"""Offline, non-authoritative BiblioAccess route and ACSM observations.

Pure helpers do no HTTP, session import, lending or fulfillment. They never
retain authorization-bearing query values. The caller must independently
validate provenance, response integrity and Core-owned identity scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit
from xml.etree import ElementTree as ET

BOOK_HOST = "www.biblioaccess.com"
TENANT = "1409"
MAX_HTML_BYTES = 2_000_000
MAX_ACSM_BYTES = 128_000
MAX_PAGE_INDEX = 100
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
    if any(ch.isspace() or ord(ch) < 32 or ch == "\\"
           or ord(ch) == 127 for ch in url):
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname != BOOK_HOST
                or parts.port not in (None, 443) or parts.username is not None
                or parts.password is not None or parts.fragment):
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


def _search_coordinates(url: str) -> tuple[dict[str, list[str]], int] | None:
    """Validate the same catalogue page for redirects, anchors and HTTP."""
    parts = _parts(url)
    if parts is None or parts.path != _SEARCH_PATH or len(url) > 8192:
        return None
    try:
        params = parse_qs(parts.query, keep_blank_values=True, max_num_fields=16)
    except ValueError:
        return None
    if (not set(params) <= _ALLOWED_SEARCH_PARAMS
            or any(len(values) != 1 or len(values[0]) > 512 for values in params.values())
            or not params.get("query", [""])[0]):
        return None
    page_token = params.get("pageindex", ["0"])[0]
    if not re.fullmatch(r"[0-9]{1,3}", page_token):
        return None
    page = int(page_token)
    return (params, page) if page <= MAX_PAGE_INDEX else None


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
        target = _search_coordinates(urljoin(request_url, location))
        if target is None:
            return "untrusted_redirect"
        source_params = parse_qs(parts.query, keep_blank_values=True)
        target_params, page = target
        if (set(source_params) != {"query"}
                or len(source_params["query"]) != 1
                or not source_params["query"][0]
                or target_params.get("query") != source_params["query"]
                or page != 0):
            return "untrusted_redirect"
        return "search_redirect"
    if status == 200:
        return "html_unparsed"
    return "unsupported"


def book_id_from_url(url: str) -> str | None:
    """Notice identifier only; never a loan ID or an entitlement."""
    parts = _parts(url)
    if parts is None or parts.query:
        return None
    match = _BOOK_PATH.fullmatch(parts.path)
    if match is None:
        return None
    # The optional slug is descriptive, but may not change the exact
    # route or book coordinate after URL/server decoding. Accept ordinary
    # UTF-8 escapes in a slug, not a second layer of escaping, encoded
    # separators, matrix parameters, dot segments or acquisition files.
    slug = parts.path[len("/1409/Catalog/Book/") + len(match.group(1)):].strip("/")
    if re.search(r"%(?![0-9a-fA-F]{2})", slug):
        return None
    try:
        decoded = unquote(slug, errors="strict")
    except UnicodeError:
        return None
    if (decoded in {".", ".."}
            or any(ch in "/\\?#;%" or ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
            or decoded.casefold().endswith((".acsm", ".lcpl", ".epub", ".pdf", ".zip"))):
        return None
    return match.group(1)


@dataclass(frozen=True)
class CatalogueLinks:
    book_ids: tuple[str, ...]
    next_page_url: str | None
    completeness: str = "partial"
    book_urls: tuple[tuple[str, str], ...] = ()
    # Short text attached to same-tenant Book anchors on the search page.
    # Not an authoritative notice; detail parsing remains separate.
    book_titles: tuple[tuple[str, str], ...] = ()


class _Links(HTMLParser):
    def __init__(self, page_url: str, *, summary_query: str | None = None):
        super().__init__(convert_charrefs=True)
        self.summary_query = summary_query
        self.summary_zero = False
        self.summary_refused = False
        self._summary_stack = []
        self._summary_text = None
        self._summary_depth = 0
        self.page_url = page_url
        current, self.current_page = _search_coordinates(page_url) or ({}, None)
        self.current_scope = {key: value for key, value in current.items() if key != "pageindex"}
        self.book_ids = []
        self.book_urls = {}
        self.book_titles = {}
        self._observed_book_titles = {}
        self._book_anchor = None
        self._book_anchor_text = []
        self.next_page_url = None
        self._next_scope = None
        self._next_conflict = False
        self._candidate = None
        self._anchor_text = []

    def _observe_next(self, link):
        # Equivalent URL encodings are acceptable; conflicting navigation
        # never selects the last anchor. Preserve the first exact URL.
        scope = parse_qs(urlsplit(link).query, keep_blank_values=True)
        if self._next_conflict:
            return
        if self._next_scope is not None and scope != self._next_scope:
            self.next_page_url = None
            self._next_conflict = True
            return
        if self.next_page_url is None:
            self.next_page_url = link
            self._next_scope = scope

    def _summary_start(self, tag, attrs):
        values = dict(attrs)
        if len(values) != len(attrs):
            self.summary_refused = True
        # A login form, book or linked result page contradicts a terminal zero.
        if (tag == "input" and str(values.get("type", "")).casefold() == "password"
                or tag == "form" and re.search(
                    r"login|logon|signin|connexion|authent", str(values.get("action", "")), re.I)
                or tag == "a" and re.search(
                    r"(?:^|/)(?:book|search)(?:[/?#]|$)", str(values.get("href", "")), re.I)):
            self.summary_refused = True
        hidden = (tag in {"head", "script", "style", "template", "noscript", "textarea",
                          "select", "button", "svg", "iframe"}
                  or "hidden" in values
                  or str(values.get("aria-hidden", "")).casefold() == "true"
                  or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden",
                                    str(values.get("style", "")), re.I))
                  or bool(set(str(values.get("class", "")).casefold().split())
                          & {"hidden", "d-none", "sr-only", "visually-hidden"})
                  or (self._summary_stack and self._summary_stack[-1][1]))
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                   "link", "meta", "param", "source", "track", "wbr"}:
            if self._summary_text is not None:
                self._summary_text.append(" ")
            return
        self._summary_stack.append((tag, bool(hidden)))
        if len(self._summary_stack) > 128:
            self.summary_refused = True
            return
        if hidden and self._summary_text is not None:
            self._summary_text = None
        if not hidden and (tag in {"p", "div", "h1", "h2", "h3", "h4", "li", "td"}
                           or tag == "span" and self._summary_text is None):
            # Keep the innermost explicit message block, not arbitrary page text.
            self._summary_text = []
            self._summary_depth = len(self._summary_stack)

    def _summary_end(self, tag):
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                   "link", "meta", "param", "source", "track", "wbr"}:
            return
        if not self._summary_stack or self._summary_stack[-1][0] != tag:
            self.summary_refused = True
            return
        if (self._summary_text is not None
                and self._summary_depth == len(self._summary_stack)):
            message = " ".join("".join(self._summary_text).split())
            prefix = "Aucun r\u00e9sultat pour la recherche "
            if message.startswith(prefix):
                if message == prefix + self.summary_query:
                    self.summary_zero = True
                else:
                    self.summary_refused = True
            self._summary_text = None
        self._summary_stack.pop()

    def handle_starttag(self, tag, attrs):
        if self.summary_query is not None:
            self._summary_start(tag, attrs)
            return
        if tag != "a":
            return
        self._book_anchor = None
        self._book_anchor_text = []
        values = dict(attrs)
        href = values.get("href")
        if (not isinstance(href, str) or not href
                or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch == "\\" for ch in href)):
            self._candidate = None
            return
        # Reject ambiguity before urljoin can remove dot segments or
        # urlsplit can normalize a malformed href. Never abort the page.
        try:
            if any(part in {".", ".."} for part in urlsplit(href).path.split("/")):
                self._candidate = None
                return
            link = urljoin(self.page_url, href)
        except ValueError:
            self._candidate = None
            return
        ident = book_id_from_url(link)
        if ident and ident not in self.book_ids and len(self.book_ids) < 300:
            self.book_ids.append(ident)
            # Preserve only a validated, same-tenant HTTPS detail URL. The
            # slug can be required for the actual book GET; never reconstruct
            # an unrelated/cross-tenant link from an opaque book identifier.
            self.book_urls[ident] = link
        if ident in self.book_urls:
            self._book_anchor = (ident, link)
        coordinates = _search_coordinates(link)
        self._candidate = None
        if coordinates is not None and self.current_page is not None:
            params, page = coordinates
            scope = {key: value for key, value in params.items() if key != "pageindex"}
            if page == self.current_page + 1 and scope == self.current_scope:
                self._candidate = link
                self._anchor_text = []
                if "next" in str(values.get("rel") or "").lower().split():
                    self._observe_next(link)

    def handle_data(self, data):
        if self.summary_query is not None:
            if self._summary_text is not None:
                self._summary_text.append(data)
            return
        if self._book_anchor is not None:
            self._book_anchor_text.append(data[:200])
        if self._candidate is not None:
            self._anchor_text.append(data[:100])

    def handle_endtag(self, tag):
        if self.summary_query is not None:
            self._summary_end(tag)
            return
        if tag == "a" and self._book_anchor is not None:
            title = " ".join("".join(self._book_anchor_text).split())
            ident, link = self._book_anchor
            # A visible generic call-to-action is not a title. Keep the
            # book link for explicit detail, not a fabricated C observation.
            if title and len(title) <= 256 and title.casefold() not in {
                "voir", "voir la fiche", "consulter", "consulter la fiche",
                "détails", "detail", "details", "en savoir plus",
            }:
                previous = self._observed_book_titles.setdefault(ident, title)
                if previous != title:
                    self.book_titles.pop(ident, None)
                    self._observed_book_titles[ident] = None
                # Another slug can contradict this title, but it cannot
                # supply a title for the first retained URL.
                elif link == self.book_urls[ident]:
                    self.book_titles[ident] = title
            self._book_anchor = None
            self._book_anchor_text = []
        if tag == "a" and self._candidate is not None:
            label = " ".join(self._anchor_text).strip().casefold()
            if label.startswith(("suiv", "next")):
                self._observe_next(self._candidate)
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
    return CatalogueLinks(tuple(parser.book_ids), parser.next_page_url,
                          book_urls=tuple(parser.book_urls.items()),
                          book_titles=tuple(parser.book_titles.items()))


def summary_attests_zero(request_url: str, html: str) -> bool:
    """Exact tenant/query and explicit standalone visible summary message.

    The phrase is attested by #7162; the original markup is not distributed.
    Any other layout, conflicting navigation or login is still unqualified.
    """
    parts = _parts(request_url)
    if not parts or parts.path != _SUMMARY_PATH:
        return False
    try:
        params = parse_qs(parts.query, keep_blank_values=True, max_num_fields=2)
    except ValueError:
        return False
    if set(params) != {"query"} or len(params["query"]) != 1:
        return False
    query = params["query"][0]
    if not query or len(query) > 512 or " ".join(query.split()) != query:
        return False
    if type(html) is not str or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        return False
    parser = _Links(request_url, summary_query=query)
    parser.feed(html)
    parser.close()
    return parser.summary_zero and not parser.summary_refused and not parser._summary_stack


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
