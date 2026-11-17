"""#6590: catalogue link validation mechanics, no transport or authority.

The caller supplies a policy that accepts a candidate URL for a specific
purpose. No global trusted host list, no network and no inferred rights.
Templates and acquisition links are not expanded or followed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from unicodedata import category
from urllib.parse import urljoin, urlsplit


def _clean(value):
    return (type(value) is str and 0 < len(value) <= 4096
            and not any(ch.isspace() or category(ch) in {"Cc", "Cf", "Cs"}
                        or ch == "\\" for ch in value))


def catalogue_url_origin(value):
    """Return exact HTTP(S) origin, or None for a structurally unsafe URI."""
    if not _clean(value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.fragment or "{" in parts.netloc or "}" in parts.netloc):
            return None
        return (parts.scheme, parts.hostname.casefold(),
                parts.port if parts.port is not None else
                (443 if parts.scheme == "https" else 80))
    except (ValueError, UnicodeError):
        return None


def resolve_catalogue_link(base, raw, *, accept):
    """Resolve a raw link, requiring the caller's explicit restrictive policy.

    No implicit same-origin or host acceptance. A caller may use the
    origin helper to impose its existing exact origin rule. The callback
    is trusted adapter code and must not perform network IO.
    """
    if not callable(accept):
        raise ValueError("invalid_catalogue_link_policy")
    if not _clean(raw) or catalogue_url_origin(base) is None:
        return None
    try:
        candidate = urljoin(base, raw)
    except (ValueError, UnicodeError):
        return None
    if catalogue_url_origin(candidate) is None:
        return None
    return candidate if accept(candidate) is True else None


@dataclass(frozen=True, slots=True, repr=False)
class CatalogueNext:
    state: str
    url: str | None = None


def catalogue_next(declared, *, resolve):
    """Distinguish absent, invalid, ambiguous and valid declarations.

    The adapter selects the relation(s) and MIME rules and supplies a
    resolver. Raw and rejected links are never returned as destinations.
    Any invalid declaration dominates, even if another link is valid.
    """
    if not callable(resolve):
        raise ValueError("invalid_catalogue_link_policy")
    values = set()
    for raw in declared:
        value = resolve(raw)
        if type(value) is not str or catalogue_url_origin(value) is None:
            return CatalogueNext("invalid")
        values.add(value)
    if not values:
        return CatalogueNext("absent")
    if len(values) != 1:
        return CatalogueNext("ambiguous")
    return CatalogueNext("valid", next(iter(values)))
