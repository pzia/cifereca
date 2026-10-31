"""Anonymous, bounded public Numilog catalogue transport (#1886 P0).

This client is NEVER the authenticated NumilogSession.client. No login cookies,
private library URLs, fulfillment or downloads cross this boundary.
"""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import math
import re
import time
import unicodedata
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError, ProviderError
from cifereca_numilog_public_cache import MAX_RATE_LIMIT_PAUSE_SECONDS
from cifereca_numilog_public import (
    SEARCH_PATH, MAX_HTML_BYTES, PublicSearchParseError, parse_public_search,
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


def fetch_public_search(query: str, *, page: int = 0, field: str = "all",
                        timeout: float = 15.0):
    """Fetch one page with a *new* cookie-free client. Reject redirects.

    Parser and HTTP errors are fixed public codes without URLs/body/credentials.
    A 403 does not justify using the authenticated session as a fallback.
    """
    if type(field) is not str or field not in _PUBLIC_FIELDS:
        raise ProviderCapabilityError("Numilog : unsupported_public_field")
    return fetch_public_search_url(public_search_url(query, page), timeout=timeout)


def fetch_public_search_url(url: str, *, timeout: float = 15.0):
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
    try:
        with HTTPX_TRANSPORT.client(
            producer="provider.numilog.public",
            timeout=timeout, follow_redirects=False, trust_env=False,
            headers={"Accept": "text/html", "Accept-Language": "fr-FR,fr;q=0.9"},
        ) as client:
            with client.stream("GET", url) as response:
                status = response.status_code
                if status in (401, 403):
                    raise ProviderError("Numilog : public_catalog_forbidden")
                if status == 429:
                    raise PublicRateLimitError("Numilog : public_catalog_rate_limited",
                                               response.headers.get("retry-after"))
                if status in (301, 302, 303, 307, 308):
                    raise ProviderError("Numilog : public_catalog_redirect_unverified")
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

    try:
        return parse_public_search(
            b"".join(chunks).decode("utf-8-sig", errors="replace"), url,
        )
    except PublicSearchParseError:
        raise ProviderError("Numilog : public_catalog_layout_unverified") from None


def fetch_public_detail(product_url: str, *, timeout: float = 15.0):
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
            with client.stream("GET", product_url) as response:
                status = response.status_code
                if status in (401, 403):
                    raise ProviderError("Numilog : public_detail_forbidden")
                if status == 429:
                    raise PublicRateLimitError("Numilog : public_detail_rate_limited",
                                               response.headers.get("retry-after"))
                if status in (301, 302, 303, 307, 308):
                    raise ProviderError("Numilog : public_detail_redirect_unverified")
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
