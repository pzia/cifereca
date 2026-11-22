"""Anonymous, bounded public Numilog catalogue transport (#1886 P0).

This client is NEVER the authenticated NumilogSession.client. No login cookies,
private library URLs, fulfillment or downloads cross this boundary.
"""
from __future__ import annotations

from email.utils import parsedate_to_datetime
from concurrent.futures import Future, TimeoutError as FlightTimeout
from copy import deepcopy
from threading import Lock
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import uuid4
import math
import re
import time
import unicodedata
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError, ProviderError
from cifereca_numilog_public_cache import MAX_RATE_LIMIT_PAUSE_SECONDS
from cifereca_numilog_public import (
    SEARCH_PATH, MAX_HTML_BYTES, PublicSearchPage, PublicSearchParseError, parse_public_search,
)

PUBLIC_ORIGIN = "https://www.numilog.com"
_PUBLIC_FIELDS = frozenset({"all"})

def _checked_timeout(timeout):
    """Reject non-finite or hostile HTTP budgets before opening a socket."""
    if type(timeout) not in (int, float):
        raise ProviderCapabilityError("Numilog : invalid_public_timeout")
    try:
        numeric = float(timeout)
    except (OverflowError, ValueError):
        raise ProviderCapabilityError("Numilog : invalid_public_timeout") from None
    if not math.isfinite(numeric) or not 0 < numeric <= 30:
        raise ProviderCapabilityError("Numilog : invalid_public_timeout")
    return numeric



class PublicRateLimitError(ProviderError):
    """Fixed public error plus a numeric delay; never retain raw headers."""
    def __init__(self, code, retry_after):
        super().__init__(code)
        self.retry_after_seconds = _retry_after_seconds(retry_after)


class PublicServiceUnavailableError(PublicRateLimitError):
    """HTTP 503 only: same bounded Retry-After parser, no implicit retry."""


def _retry_after_seconds(value, *, now=None):
    """Read delay-seconds or HTTP-date without retaining untrusted text."""
    if type(value) is not str or len(value) > 128:
        return 0.0
    value = value.strip()
    if re.fullmatch(r'[0-9]+', value):
        # int() safely parses <=128 digits. float() on "9"*100 can yield
        # astronomically large deadlines; longer input previously became
        # infinity and blocked even a freshly reconnected session.
        return float(min(int(value), MAX_RATE_LIMIT_PAUSE_SECONDS))
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return 0.0
        return float(min(
            MAX_RATE_LIMIT_PAUSE_SECONDS,
            max(0.0, date.timestamp() - (time.time() if now is None else now)),
        ))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def public_search_url(query: str, page: int = 0) -> str:
    """Only one observed public brSimple route, never arbitrary URL input."""
    if (type(query) is not str or not 2 <= len(query.strip()) <= 200
            or any(unicodedata.category(ch) in {"Cc", "Cf", "Cs"} for ch in query)):
        raise ProviderCapabilityError("Numilog : invalid_public_query")
    if type(page) is not int or not 0 <= page <= 100:
        raise ProviderCapabilityError("Numilog : invalid_public_page")
    params = [("mode", "brSimple"), ("query", query.strip())]
    if page:
        params.append(("GRID_PAGE", str(page)))
    return PUBLIC_ORIGIN + SEARCH_PATH + "?" + urlencode(params)


# Align with the Core receipt's byte bound. Larger accepted pages remain
# usable for Search, but cannot be retained for this experimental replay.
_MAX_RETAINED_BYTES = 2 * 1024 * 1024
_PARSER_FAMILY_DIGEST = sha256(b"numilog.public.search.html").hexdigest()
# Bump with changes to the replay parser and its dependent HTML helpers.
# v6: refuse undecodable UTF-8 instead of projecting replacement characters (#7163).
# Keep the family digest: retained earlier inputs can be replayed safely.
_PARSER_IMPLEMENTATION_DIGEST = sha256(b"numilog.public.search.html.v6").hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class _RetainedSearchInput:
    token: str
    transport_digest: str
    input_digest: str
    parser_family_digest: str
    parser_implementation_digest: str
    _url: str = field(repr=False)
    _body: bytes = field(repr=False)

    def __repr__(self):
        return "_RetainedSearchInput(private)"


@dataclass(frozen=True, slots=True, repr=False)
class _RetainedSearchPage:
    page: PublicSearchPage
    raw_input: _RetainedSearchInput | None


def _transport_digest(url):
    return sha256(
        ("numilog.public.search.get.v1\0text/html\0fr-FR,fr;q=0.9\0" + url).encode("utf-8")
    ).hexdigest()


def _retained_search_input(body, url):
    if not 1 <= len(body) <= _MAX_RETAINED_BYTES:
        return None
    transport = _transport_digest(url)
    return _RetainedSearchInput(
        uuid4().hex, transport, sha256(body).hexdigest(), _PARSER_FAMILY_DIGEST,
        _PARSER_IMPLEMENTATION_DIGEST, url, body,
    )


# Closed parser reasons already emitted by the shared deterministic parser.
# Keep the coarse ProviderError prefix for compatibility; upstream text,
# query strings and HTML are never included in the diagnostic reason.
_LAYOUT_REASONS = frozenset({
    "ambiguous_html_attribute", "hidden_field", "ambiguous_field", "invalid_field",
    "invalid_search_url", "invalid_product_url", "ambiguous_price", "unqualified_price",
    "ambiguous_product", "missing_title", "ambiguous_metadata", "invalid_authors",
    "ambiguous_cover", "invalid_formats", "html_size", "html_structure",
    "unknown_search_layout", "unqualified_total", "inconsistent_results",
    "duplicate_product", "ambiguous_pagination", "inconsistent_pagination",
    "inconsistent_page", "excessive_pagination", "pagination_query_changed",
    "invalid_pagination", "invalid_facets", "ambiguous_sort", "invalid_sort",
    "invalid_encoding",
})


def _parse_search_body(body, url):
    # The transport and retained replay must see identical source bytes.
    # Replacement decoding can turn an invalid supplier title/identifier into
    # apparently verified public C text; never publish such a false fact.
    try:
        html = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        error = ProviderError("Numilog : public_catalog_layout_unverified:invalid_encoding")
        error.code = "search_protocol_unverified"
        raise error from None
    try:
        return parse_public_search(html, url)
    except PublicSearchParseError as exc:
        reason = exc.code if type(exc.code) is str and exc.code in _LAYOUT_REASONS else "unknown"
        error = ProviderError("Numilog : public_catalog_layout_unverified:" + reason)
        error.code = "search_protocol_unverified"
        raise error from None


def _parse_retained_search_input(raw):
    """Pure replay of the exact parser input; never a transport operation."""
    if (type(raw) is not _RetainedSearchInput
            or type(raw._body) is not bytes
            or not 1 <= len(raw._body) <= _MAX_RETAINED_BYTES
            or sha256(raw._body).hexdigest() != raw.input_digest
            or type(raw._url) is not str
            or len(raw._url) > 4096
            or _transport_digest(raw._url) != raw.transport_digest
            or raw.parser_family_digest != _PARSER_FAMILY_DIGEST):
        raise ProviderError("Numilog : public_catalog_replay_invalid")
    return _parse_search_body(raw._body, raw._url)


def fetch_public_search(query: str, *, page: int = 0, field: str = "all",
                        timeout: float = 15.0, _retain_input: bool = False):
    """Fetch one page with a *new* cookie-free client. Reject redirects.

    Parser and HTTP errors are fixed public codes without URLs/body/credentials.
    A 403 does not justify using the authenticated session as a fallback.
    """
    if type(field) is not str or field not in _PUBLIC_FIELDS:
        raise ProviderCapabilityError("Numilog : unsupported_public_field")
    if _retain_input is True:
        return fetch_public_search_url(public_search_url(query, page), timeout=timeout,
                                       _retain_input=True)
    return fetch_public_search_url(public_search_url(query, page), timeout=timeout)


# One in-flight read per exact anonymous transport, not another TTL cache.
# Keep existing session-local budgets, epochs and replay receipts independent.
# The digest describes the fixed GET producer/headers and the exact URL.
_PUBLIC_SEARCH_FLIGHTS = {}
_PUBLIC_SEARCH_FLIGHT_LOCK = Lock()
_MAX_PUBLIC_SEARCH_FLIGHTS = 16


@dataclass(frozen=True)
class _SharedSearchFailure:
    message: str
    retry_after: float | None = None
    code: str | None = None


def _shareable_failure(exc):
    if type(exc) is PublicRateLimitError:
        return _SharedSearchFailure("Numilog : public_catalog_rate_limited", exc.retry_after_seconds)
    if type(exc) is PublicServiceUnavailableError:
        return _SharedSearchFailure("Numilog : public_catalog_unavailable", exc.retry_after_seconds)
    if type(exc) is ProviderError:
        message = str(exc)
        prefix = "Numilog : public_catalog_"
        if message in {prefix + suffix for suffix in (
                "forbidden", "redirect_unverified", "unavailable", "unexpected_mime",
                "response_too_large", "network_unavailable")}:
            return _SharedSearchFailure(message)
        layout = prefix + "layout_unverified:"
        if message.startswith(layout) and message[len(layout):] in _LAYOUT_REASONS | {"unknown"}:
            code = getattr(exc, "code", None)
            return _SharedSearchFailure(
                message, code="search_protocol_unverified"
                if type(code) is str and code == "search_protocol_unverified" else None,
            )
    return _SharedSearchFailure("Numilog : public_catalog_network_unavailable")


def _await_public_search_flight(future, timeout):
    try:
        outcome = future.result(timeout=timeout)
    except FlightTimeout:
        # Never start another GET simply because one waiter ran out of budget.
        raise ProviderError("Numilog : public_catalog_request_in_progress") from None
    if type(outcome) is _SharedSearchFailure:
        if outcome.retry_after is not None:
            exception = (PublicServiceUnavailableError
                         if outcome.message == "Numilog : public_catalog_unavailable"
                         else PublicRateLimitError)
            error = exception(outcome.message, None)
            error.retry_after_seconds = outcome.retry_after
            raise error
        error = ProviderError(outcome.message)
        if outcome.code == "search_protocol_unverified":
            error.code = outcome.code
        raise error
    page, body = outcome
    return deepcopy(page), body


def _coalesced_public_search(url, timeout):
    # The timeout is a per-caller wait budget, not part of the anonymous GET's
    # URL/headers. Different budgets must not create simultaneous duplicate
    # transports; the leader retains its own HTTP timeout and each follower
    # independently applies its wait timeout in _await_public_search_flight.
    key = _transport_digest(url)
    with _PUBLIC_SEARCH_FLIGHT_LOCK:
        flight = _PUBLIC_SEARCH_FLIGHTS.get(key)
        leader = flight is None and len(_PUBLIC_SEARCH_FLIGHTS) < _MAX_PUBLIC_SEARCH_FLIGHTS
        if leader:
            flight = Future()
            _PUBLIC_SEARCH_FLIGHTS[key] = flight
    if flight is None:
        # Saturation must NOT turn a bounded shared public transport into an
        # unbounded independent GET. The existing provider maps this fixed
        # retryable busy result to partial/budget_busy, never a verified zero.
        # A matching in-flight key above can still join even at capacity.
        raise ProviderError("Numilog : public_catalog_request_in_progress")
    if not leader:
        return _await_public_search_flight(flight, timeout)
    try:
        page, body = _fetch_public_search_page(url, timeout, None)
        # No mutable SearchResult DTO, cache generation or retained token is
        # shared between callers. The original parsed page stays with leader.
        outcome = (deepcopy(page), body)
    except BaseException as exc:
        with _PUBLIC_SEARCH_FLIGHT_LOCK:
            _PUBLIC_SEARCH_FLIGHTS.pop(key, None)
            flight.set_result(_shareable_failure(exc))
        raise
    with _PUBLIC_SEARCH_FLIGHT_LOCK:
        _PUBLIC_SEARCH_FLIGHTS.pop(key, None)
        flight.set_result(outcome)
    return page, body


def fetch_public_search_url(url: str, *, timeout: float = 15.0,
                            _retain_input: bool = False, _before_get=None):
    """Follow only an already-attested public pager/facet URL.

    The session enforces source provenance before calling this transport.
    Here enforce the same strict public query/page bounds as ordinary search
    before sending any HTTP request; never accept signed/private parameters.
    """
    from cifereca_numilog_public import _query
    try:
        parsed = _query(url)
    except PublicSearchParseError:
        raise ProviderCapabilityError("Numilog : invalid_public_navigation_url") from None
    # The parser accepts a wider set of page values for offline inspection;
    # live requests must retain the narrower provider budget.
    public_search_url(parsed["query"], int(parsed.get("GRID_PAGE", "0")))
    timeout = _checked_timeout(timeout)
    # An explicit refresh has its own late authority guard and must actually
    # start its own transport; it cannot satisfy that guard with shared data.
    if _before_get is None:
        page, body = _coalesced_public_search(url, timeout)
    else:
        page, body = _fetch_public_search_page(url, timeout, _before_get)
    # Replay receipts remain unique to each session even when one anonymous
    # in-flight GET served multiple independent callers.
    if _retain_input is True:
        return _RetainedSearchPage(page, _retained_search_input(body, url))
    return page


def _fetch_public_search_page(url, timeout, _before_get):
    try:
        with HTTPX_TRANSPORT.client(
            producer="provider.numilog.public",
            timeout=timeout, follow_redirects=False, trust_env=False,
            headers={"Accept": "text/html", "Accept-Language": "fr-FR,fr;q=0.9"},
        ) as client:
            # Private refresh port linearizes transport start as late as
            # possible, AFTER client construction and BEFORE the GET.
            # It performs no IO and does not hold locks across the stream.
            if _before_get is not None:
                _before_get()
            with client.stream("GET", url) as response:
                status = response.status_code
                if status in (401, 403):
                    raise ProviderError("Numilog : public_catalog_forbidden")
                if status == 429:
                    raise PublicRateLimitError("Numilog : public_catalog_rate_limited",
                                               response.headers.get("retry-after"))
                if status in (301, 302, 303, 307, 308):
                    raise ProviderError("Numilog : public_catalog_redirect_unverified")
                if status == 503:
                    # Respect the supplier's bounded retry window: the cache
                    # must not repeatedly GET the same unavailable public page.
                    raise PublicServiceUnavailableError(
                        "Numilog : public_catalog_unavailable",
                        response.headers.get("retry-after"),
                    )
                if status != 200:
                    raise ProviderError("Numilog : public_catalog_unavailable")
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if mime not in ("text/html", "application/xhtml+xml"):
                    raise ProviderError("Numilog : public_catalog_unexpected_mime")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_HTML_BYTES:
                        raise ProviderError("Numilog : public_catalog_response_too_large")
                    chunks.append(chunk)
    except httpx.HTTPError:
        raise ProviderError("Numilog : public_catalog_network_unavailable") from None

    body = b"".join(chunks)
    return _parse_search_body(body, url), body


def fetch_public_detail(product_url: str, *, timeout: float = 15.0, _before_get=None):
    """Explicit on-demand, anonymous product detail; never called by sync/search.

    No redirects, account cookies, signed endpoints or unbounded responses.
    """
    from cifereca_numilog_html import public_book_url
    from cifereca_numilog_public_detail import (
        MAX_DETAIL_BYTES, PublicDetailError, parse_public_detail_jsonld,
    )
    if (type(product_url) is not str
            or public_book_url(product_url, base_url=PUBLIC_ORIGIN + "/") != product_url
            or not product_url.startswith(PUBLIC_ORIGIN + "/")):
        raise ProviderCapabilityError("Numilog : invalid_public_product_url")
    timeout = _checked_timeout(timeout)
    try:
        with HTTPX_TRANSPORT.client(
            producer="provider.numilog.public",
            timeout=timeout, follow_redirects=False, trust_env=False,
            headers={"Accept": "text/html", "Accept-Language": "fr-FR,fr;q=0.9"},
        ) as client:
            if _before_get is not None:
                _before_get()
            with client.stream("GET", product_url) as response:
                status = response.status_code
                if status in (401, 403):
                    raise ProviderError("Numilog : public_detail_forbidden")
                if status == 429:
                    raise PublicRateLimitError("Numilog : public_detail_rate_limited",
                                               response.headers.get("retry-after"))
                if status in (301, 302, 303, 307, 308):
                    raise ProviderError("Numilog : public_detail_redirect_unverified")
                if status == 503:
                    raise PublicServiceUnavailableError("Numilog : public_detail_unavailable",
                                                        response.headers.get("retry-after"))
                if status != 200:
                    raise ProviderError("Numilog : public_detail_unavailable")
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if mime not in ("text/html", "application/xhtml+xml"):
                    raise ProviderError("Numilog : public_detail_unexpected_mime")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_DETAIL_BYTES:
                        raise ProviderError("Numilog : public_detail_response_too_large")
                    chunks.append(chunk)
    except httpx.HTTPError:
        raise ProviderError("Numilog : public_detail_network_unavailable") from None
    try:
        return parse_public_detail_jsonld(
            b"".join(chunks).decode("utf-8-sig", errors="replace"), product_url,
        )
    except PublicDetailError:
        raise ProviderError("Numilog : public_detail_layout_unverified") from None


def fetch_guarded_purchase_detail(cache, product_url, expected_title, *, check_authority,
                                  with_observation=False):
    """Provider-private transport adapter; the Core owns the actual guard.

    Reuse the existing public cache/key/budget, including on 429. The
    title stays local to the comparison (never sent in a query), and the
    result remains an independent public product, NOT a purchased edition.
    Authority must also be rechecked by the Core writer at final commit.
    No automatic caller or fallback to Search/Readzis/private HTTP.
    """
    from cifereca_numilog_html import public_book_url
    from cifereca_numilog_public_cache import PublicCatalogueCache
    from cifereca_numilog_public_detail import (
        PublicDetailError, validated_public_jsonld_fields,
    )
    error = "Numilog : public_purchase_detail_unverified"

    def title_key(value):
        if (type(value) is not str or not 1 <= len(value) <= 512
                or not value.strip()
                or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value)):
            return None
        return unicodedata.normalize("NFKC", " ".join(value.split())).casefold()

    expected = title_key(expected_title)
    if (type(cache) is not PublicCatalogueCache or not callable(check_authority)
            or expected is None or type(product_url) is not str
            or public_book_url(product_url, base_url=PUBLIC_ORIGIN + "/") != product_url
            or not product_url.startswith(PUBLIC_ORIGIN + "/")):
        raise ProviderCapabilityError(error)

    def checked():
        if check_authority() is not True:
            raise ProviderCapabilityError("Numilog : public_purchase_authority_revoked")

    def before_get():
        start = getattr(check_authority, "hold_transport_start", None)
        if callable(start):
            with start() as allowed:
                if allowed is not True:
                    raise ProviderCapabilityError("Numilog : public_purchase_authority_revoked")
        else:
            checked()

    checked()
    detail, observed_at = cache.get_observed(
        ("detail", product_url),
        lambda: fetch_public_detail(product_url, _before_get=before_get),
    )
    checked()
    if (type(detail) is not dict or detail.get("product_url") != product_url
            or detail.get("owned") is not False or detail.get("downloadable") is not False
            or detail.get("download_url", False) is not None):
        raise ProviderCapabilityError(error)
    try:
        fields, _evidence = validated_public_jsonld_fields(detail, product_url)
    except PublicDetailError:
        raise ProviderCapabilityError(error) from None
    if title_key(fields.get("title")) != expected:
        raise ProviderCapabilityError("Numilog : public_purchase_title_conflict")
    return (detail, observed_at) if with_observation else detail



def resolve_readzis_recommendation(recommendation_url: str, *, lid: str,
                                   timeout: float = 15.0) -> str:
    """One explicit anonymous share GET; NEVER follow the marketing Location.

    A visit to Readzis may be tracked commercially. Return only the strict,
    same-ID, query-free canonical public book URL; no delivery/ACSM rights.
    """
    from cifereca_numilog_html import readzis_recommendation_url, public_book_url
    if (type(recommendation_url) is not str
            or readzis_recommendation_url(
                recommendation_url, lid,
                PUBLIC_ORIGIN + '/Pages/Compte/MesLivres.aspx',
            ) != recommendation_url):
        raise ProviderCapabilityError("Numilog : invalid_public_readzis_url")
    timeout = _checked_timeout(timeout)
    try:
        with HTTPX_TRANSPORT.client(
            producer="provider.numilog.public",
            timeout=timeout, follow_redirects=False, trust_env=False,
            headers={"Accept": "text/html", "Accept-Language": "fr-FR,fr;q=0.9"},
        ) as client:
            with client.stream("GET", recommendation_url) as response:
                status = response.status_code
                if status in (401, 403):
                    raise ProviderError("Numilog : public_readzis_forbidden")
                if status == 429:
                    raise PublicRateLimitError(
                        "Numilog : public_readzis_rate_limited",
                        response.headers.get("retry-after"),
                    )
                if status not in (301, 302):
                    raise ProviderError("Numilog : public_readzis_redirect_unverified")
                location = response.headers.get("location")
    except httpx.HTTPError:
        raise ProviderError("Numilog : public_readzis_network_unavailable") from None

    if type(location) is not str or not 0 < len(location) <= 4096:
        raise ProviderError("Numilog : public_readzis_redirect_unverified")
    try:
        parsed = urlsplit(location)
        if (parsed.scheme != "https" or parsed.netloc != "www.numilog.com"
                or parsed.fragment or parsed.username or parsed.password
                or chr(92) in parsed.path or any(ch.isspace() for ch in parsed.path)
                or any(segment in (".", "..") for segment in parsed.path.split("/"))):
            raise ValueError("untrusted location")
        # The marketing query is opaque and irrelevant: do not inspect,
        # validate, persist, log or follow it. Only the query-free product
        # destination is eligible for a separate anonymous GET.
        canonical = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if (public_book_url(canonical, base_url=PUBLIC_ORIGIN + "/") != canonical
                or parsed.path.split("/")[1] != lid):
            raise ValueError("different product")
        return canonical
    except (ValueError, IndexError, TypeError, AttributeError):
        raise ProviderError("Numilog : public_readzis_redirect_unverified") from None
