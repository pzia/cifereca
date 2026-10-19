"""Closed, bounded public search source-status projection for GTK (#1916).

Use only attested Core enum/error codes. No provider labels, IDs, exception
messages, URLs or private identity/credential values enter visible text.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

KNOWN = frozenset({
    "live", "cached", "partial", "known_catalog_stale", "unavailable",
    "fallback_global", "access_denied", "authentication_required",
})
_ACCESS = {"SearchAccessDenied": "access_denied",
           "SearchAuthenticationRequired": "authentication_required"}
_CAPTIONS = (
    ("known_catalog_stale", "secours local"),
    ("authentication_required", "authentification requise"),
    ("access_denied", "accès refusé"),
    ("unavailable", "indisponible"),
    ("partial", "résultats partiels"),
    ("fallback_global", "repli global"),
    ("cached", "en cache"),
    ("live", "en ligne"),
)


def public_batch_status(batch: Any) -> str | None:
    """Map one Core batch into a closed status; never echo a plugin value."""
    raw = getattr(batch, "result_status", None)
    code = getattr(batch, "error_code", None)
    if type(raw) is not str or raw not in KNOWN:
        return None
    if raw == "unavailable" and type(code) is str and code in _ACCESS:
        return _ACCESS[code]
    return raw


def safe_source_statuses(statuses: object) -> tuple[str, str]:
    """Compact visible caption and full trusted tooltip; empty if no evidence.

    Caller must hold a generation- and identity-scoped provider map. The keys
    are discarded entirely; status values must be exact built-in strings.
    """
    if type(statuses) is not dict:
        return "", ""
    values = list(statuses.values())[:64]
    counts = Counter(value for value in values
                     if type(value) is str and value in KNOWN)
    if not counts:
        return "", ""
    parts = [
        f"{counts[key]} source{'s' if counts[key] > 1 else ''} {label}"
        for key, label in _CAPTIONS if counts[key]
    ]
    # Keep narrow window readable; expose the full list through a tooltip.
    caption = " · ".join(parts[:2])
    if len(parts) > 2:
        caption += " · autres états"
    tooltip = "Recherche par source : " + " ; ".join(parts)
    return " · " + caption, tooltip
