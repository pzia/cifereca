"""Closed, non-identifying Paris/PNB transport trace fields.

A URL hostname is not inherently public: credentials, opaque subdomains and
signed paths must not escape through diagnostic messages. Unknown upstreams
are reported as a class, not echoed. Never accept loan or account IDs here.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_PUBLIC_BOOK_ID = re.compile(r"[0-9]{13}\Z", re.ASCII)
_PUBLIC_SITES = frozenset({
    "bibliotheques.paris.fr",
    "pnb-dilicom.centprod.com",
    "marketplace.syracuse.cloud",
    "numilog.com",
    "www.edenlivres.fr",
    "www.cantook.net",
    "acs4.edelivres.fr",
    "acs4.edenlivres.fr",
    "acs4.cantook.net",
})
_PUBLIC_CONTENT_TYPES = frozenset({
    "application/vnd.adobe.adept+xml",
    "application/epub+zip",
    "application/pdf",
    "application/octet-stream",
    "application/json",
    "text/html",
    "text/xml",
    "application/xml",
})


def public_book_id(value: object) -> str:
    """An exact 13 ASCII-digit catalogue EAN is publishable; no loan ID."""
    return value if type(value) is str and _PUBLIC_BOOK_ID.fullmatch(value) else "unavailable"


def public_site(value: object) -> str:
    """Expose only fixed known public HTTPS hosts, not arbitrary domain text."""
    if type(value) is not str or not 1 <= len(value) <= 8192:
        return "unknown"
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.username is not None
                or url.password is not None or url.port not in (None, 443)):
            return "unknown"
        host = (url.hostname or "").casefold()
        return host if host in _PUBLIC_SITES else "external"
    except ValueError:
        return "unknown"


def public_http_status(value: object) -> int | str:
    return value if type(value) is int and 100 <= value <= 599 else "unknown"


def public_content_type(value: object) -> str:
    if type(value) is not str or len(value) > 256:
        return "other"
    normalized = value.split(";", 1)[0].strip().casefold()
    return normalized if normalized in _PUBLIC_CONTENT_TYPES else "other"
