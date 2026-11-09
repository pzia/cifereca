"""Tenant 1409 catalogue HTTP read adapter (issue #3691).

Uses only Core's HTTPX_TRANSPORT; never opens a browser or writes a library
entitlement. SearchResultSummary may redirect to Search. A summary HTTP 200 is
*not* proof of zero results, and an empty Search page is not an exhaustive
catalogue assertion. No session or profile is implicitly shared.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
from biblioaccess_metadata import BibliographicNotice, parse_book_metadata
from biblioaccess_observations import (
    MAX_HTML_BYTES, _SEARCH_PATH, _SUMMARY_PATH, _ALLOWED_SEARCH_PARAMS,
    _parts, classify_search_response, extract_catalogue_links, search_summary_url,
)

MAX_PAGES = 3  # Legacy bounded multi-page diagnostic.
MAX_PAGE_INDEX = 100  # Public UI page requests, zero-indexed.


class CatalogueHTTPError(ValueError):
    """Technical closed codes, no remote URL/body/credentials interpolated."""


@dataclass(frozen=True)
class CatalogueSearchObservation:
    book_ids: tuple[str, ...]
    pages_observed: int
    status: str
    completeness: str = "partial"
    authenticated: bool = False
    entitlements_verified: bool = False
    book_urls: tuple[tuple[str, str], ...] = ()
    book_titles: tuple[tuple[str, str], ...] = ()


def _checked_search_url(url: str, query: str, *, page: int | None = None) -> None:
    u = _parts(url)
    if u is None or u.path != _SEARCH_PATH or len(url) > 8192:
        raise CatalogueHTTPError("catalogue_url_refused")
    try:
        params = parse_qs(u.query, keep_blank_values=True, max_num_fields=16)
    except ValueError:
        raise CatalogueHTTPError("catalogue_url_refused") from None
    if (not set(params) <= _ALLOWED_SEARCH_PARAMS or
            any(len(v) != 1 or len(v[0]) > 512 for v in params.values()) or
            params.get("query") != [query] or
            not (params.get("pageindex", ["0"])[0].isdigit()) or
            int(params.get("pageindex", ["0"])[0]) > 100):
        raise CatalogueHTTPError("catalogue_url_refused")
    if page is not None and int(params.get("pageindex", ["0"])[0]) != page:
        raise CatalogueHTTPError("catalogue_pagination_refused")


def _read_html(response) -> str:
    media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media not in ("text/html", "application/xhtml+xml"):
        raise CatalogueHTTPError("catalogue_unexpected_content")
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > MAX_HTML_BYTES:
            raise CatalogueHTTPError("catalogue_body_limit")
        body.extend(chunk)
    try:
        return body.decode("utf-8", errors="strict")
    except UnicodeError:
        raise CatalogueHTTPError("catalogue_bad_encoding") from None


class BiblioAccessCatalogueHTTP:
    """One isolated HTTPX client, explicit bounded search, never mutations."""

    def __init__(self, *, transport=None):
        self.http = HTTPX_TRANSPORT.client(
            producer="provider.biblioaccess",
            timeout=httpx.Timeout(12, connect=8), follow_redirects=False,
            trust_env=False, transport=transport,
        )
        self._lock = Lock()
        self._closed = False
        self._last_search_ids: frozenset[str] = frozenset()
        self._last_book_urls: dict[str, str] = {}

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._last_search_ids = frozenset()
        self._last_book_urls = {}
        self.http.cookies.clear()
        self.http.close()

    def search(self, query: str, *, max_pages: int = 1) -> CatalogueSearchObservation:
        """At most MAX_PAGES; no implicit login and no pagination outside 1409."""
        if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
            raise CatalogueHTTPError("catalogue_page_limit")
        try:
            summary = search_summary_url(query)
        except ValueError:
            raise CatalogueHTTPError("catalogue_invalid_query") from None
        normalized_query = parse_qs(urlsplit(summary).query)["query"][0]
        with self._lock:
            if self._closed:
                raise CatalogueHTTPError("catalogue_client_closed")
            # Avoid parallel cookie jar mutation or interleaved pagination.
            self._last_search_ids = frozenset()
            self._last_book_urls = {}
            result = self._search_locked(summary, normalized_query, max_pages)
            self._last_search_ids = frozenset(result.book_ids)
            self._last_book_urls = dict(result.book_urls)
            return result

    def search_page(self, query: str, *, page: int = 0) -> CatalogueSearchObservation:
        """Read the requested zero-indexed page using upstream next-links.

        Keep each page's IDs separate. The upstream navigation is a linked
        pager, so reaching page N may require N preceding GETs; no title
        pages or results are invented if the chain ends early.
        """
        if type(page) is not int or not 0 <= page <= MAX_PAGE_INDEX:
            raise CatalogueHTTPError("catalogue_page_limit")
        try:
            summary = search_summary_url(query)
        except ValueError:
            raise CatalogueHTTPError("catalogue_invalid_query") from None
        normalized_query = parse_qs(urlsplit(summary).query)["query"][0]
        with self._lock:
            if self._closed:
                raise CatalogueHTTPError("catalogue_client_closed")
            self._last_search_ids = frozenset()
            self._last_book_urls = {}
            result = self._search_locked(
                summary, normalized_query, page + 1, target_page=page)
            self._last_search_ids = frozenset(result.book_ids)
            self._last_book_urls = dict(result.book_urls)
            return result

    def _search_locked(self, summary, query, max_pages, *, target_page=None):
        try:
            with self.http.stream(
                "GET", summary, follow_redirects=False,
                headers={"Accept": "text/html"},
            ) as response:
                status = classify_search_response(
                    request_url=summary, status=response.status_code,
                    location=response.headers.get("location", ""))
                if status == "html_unparsed":
                    _read_html(response)
                    return CatalogueSearchObservation(
                        (), 0, "summary_html_unverified")
                if status != "search_redirect":
                    raise CatalogueHTTPError({
                        "authentication_required": "catalogue_auth_required",
                        "rate_limited": "catalogue_rate_limited",
                        "remote_error": "catalogue_remote_failure",
                    }.get(status, "catalogue_redirect_refused"))
                target = urljoin(summary, response.headers["location"])
            _checked_search_url(target, query)
            seen = set()
            book_urls = {}
            book_titles = {}
            page_count = 0
            expected_page = None
            while page_count < max_pages:
                if expected_page is not None:
                    _checked_search_url(target, query, page=expected_page)
                with self.http.stream(
                    "GET", target, follow_redirects=False,
                    headers={"Accept": "text/html"},
                ) as page:
                    if page.status_code != 200:
                        raise CatalogueHTTPError({
                            401: "catalogue_auth_required",
                            403: "catalogue_auth_required",
                            429: "catalogue_rate_limited",
                        }.get(page.status_code, "catalogue_page_unavailable"))
                    html = _read_html(page)
                links = extract_catalogue_links(target, html)
                if target_page is None or page_count == target_page:
                    seen.update(links.book_ids)
                    for ident, detail_url in links.book_urls:
                        book_urls.setdefault(ident, detail_url)
                    for ident, title in links.book_titles:
                        book_titles.setdefault(ident, title)
                page_count += 1
                if links.next_page_url is None:
                    return CatalogueSearchObservation(
                        tuple(sorted(seen)), page_count,
                        ("page_out_of_range_unverified"
                         if target_page is not None and page_count <= target_page
                         else "page_read_unverified"),
                        book_urls=tuple(sorted(book_urls.items())),
                        book_titles=tuple(sorted(book_titles.items())))
                current = parse_qs(urlsplit(target).query)
                expected_page = int(current.get("pageindex", ["0"])[0]) + 1
                _checked_search_url(links.next_page_url, query, page=expected_page)
                target = links.next_page_url
            return CatalogueSearchObservation(
                tuple(sorted(seen)), page_count, "page_budget_exhausted",
                book_urls=tuple(sorted(book_urls.items())),
                book_titles=tuple(sorted(book_titles.items())))
        except httpx.HTTPError:
            raise CatalogueHTTPError("catalogue_transport_unavailable") from None


    def read_observed_notice(self, book_id: str) -> BibliographicNotice | None:
        """Read only one book ID observed in this client's last search.

        Never enumerate arbitrary IDs, infer ownership or follow redirects.
        A missing JSON-LD Book is unknown, not a missing or unavailable book.
        """
        if (type(book_id) is not str or not book_id.isascii()
                or not book_id.isdigit() or not 1 <= len(book_id) <= 18):
            raise CatalogueHTTPError("catalogue_book_id_refused")
        with self._lock:
            if self._closed:
                raise CatalogueHTTPError("catalogue_client_closed")
            if book_id not in self._last_search_ids:
                raise CatalogueHTTPError("catalogue_book_not_observed")
            url = self._last_book_urls.get(book_id)
            if url is None or _parts(url) is None:
                raise CatalogueHTTPError("catalogue_book_url_missing")
            try:
                with self.http.stream(
                    "GET", url, follow_redirects=False,
                    headers={"Accept": "text/html"},
                ) as response:
                    if response.status_code != 200:
                        raise CatalogueHTTPError({
                            401: "catalogue_auth_required",
                            403: "catalogue_auth_required",
                            429: "catalogue_rate_limited",
                        }.get(response.status_code, "catalogue_book_unavailable"))
                    html = _read_html(response)
                return parse_book_metadata(url, html)
            except httpx.HTTPError:
                raise CatalogueHTTPError("catalogue_transport_unavailable") from None
