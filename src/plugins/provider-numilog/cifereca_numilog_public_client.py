"""Anonymous, bounded public Numilog catalogue transport (#1886 P0).

This client is NEVER the authenticated NumilogSession.client. No login cookies,
private library URLs, fulfillment or downloads cross this boundary.
"""
from __future__ import annotations

import unicodedata
from urllib.parse import urlencode

import httpx

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog_public import (
    SEARCH_PATH, MAX_HTML_BYTES, PublicSearchParseError, parse_public_search,
)

PUBLIC_ORIGIN = "https://www.numilog.com"
_PUBLIC_FIELDS = frozenset({"all"})


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
    url = public_search_url(query, page)
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=False, trust_env=False,
            headers={"User-Agent": "Cifereca-Numilog-Public/1.0",
                     "Accept": "text/html", "Accept-Language": "fr-FR,fr;q=0.9"},
        ) as client:
            with client.stream("GET", url) as response:
                status = response.status_code
                if status in (401, 403):
                    raise ProviderError("Numilog : public_catalog_forbidden")
                if status == 429:
                    raise ProviderError("Numilog : public_catalog_rate_limited")
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
