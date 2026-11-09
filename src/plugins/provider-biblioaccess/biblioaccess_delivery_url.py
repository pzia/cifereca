"""Qualified, credential-free delivery URL boundaries for tenant 1409.

An authenticated MyLoans row, not the hostname, establishes a positive
download offer. This validator only constrains the subsequent network
destination; it never asserts entitlement, fulfillment or a license.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_PNB = "pnb-dilicom.centprod.com"
_NUMILOG = "entrepot2r.numilog.com"
# Exact supplier delivery hosts already supported by the PNB ecosystem.
# Other destinations fail closed pending an observed and approved route.
_REDIRECT_HOSTS = frozenset({
    _PNB, _NUMILOG,
    "www.edenlivres.fr", "www.cantook.net",
    "acs4.edelivres.fr", "acs4.edenlivres.fr", "acs4.cantook.net",
})
_UNSAFE_ESCAPE = re.compile(r"%(?:2e|2f|5c|00|0[ad])", re.I)


def _parsed_safe(url: str):
    if (type(url) is not str or not 0 < len(url) <= 8192
            or any(ord(c) < 33 or ord(c) > 126 or c == "\\"
                   for c in url)):
        return None
    try:
        part = urlsplit(url)
        if (part.scheme != "https" or part.username is not None
                or part.password is not None or part.port not in (None, 443)
                or part.fragment or not part.hostname
                or part.hostname.endswith(".") or "%" in part.netloc
                or not part.path.startswith("/") or len(part.path) > 2048
                or "//" in part.path or _UNSAFE_ESCAPE.search(part.path)
                or any(segment in (".", "..") for segment in part.path.split("/"))):
            return None
        return part
    except (TypeError, ValueError):
        return None


def classify_loan_delivery(url: str) -> str | None:
    """Only two positively observed initial MyLoans action shapes."""
    part = _parsed_safe(url)
    if part is None:
        return None
    if (part.hostname == _NUMILOG
            and part.path == "/fulfillment/URLLink.acsm"
            and bool(part.query)):
        return "numilog_acsm"
    if (part.hostname == _PNB
            and part.path.endswith("/do")
            and len(part.path.split("/")) >= 3):
        return "pnb_do"
    return None


def allowed_delivery_redirect(url: str) -> bool:
    """An external hop carries NO institution cookies or login headers."""
    part = _parsed_safe(url)
    return part is not None and part.hostname in _REDIRECT_HOSTS
