"""Kobo private signed-URL diagnostics: explicitly trace-only, never public ABI.

Only the activated local trace level may emit full acquisition URLs. This
code never serializes Vault bearer/refresh tokens, request headers or a full
descriptor. Log entries use %r so control characters cannot forge log lines.
"""
from __future__ import annotations

import logging

from cifereca.plugin_api import HTTPX_TRANSPORT

_LOG = logging.getLogger("cifereca")
_MAX_URL_CHARS = 16384
_HANDLING = frozenset({"http_plain", "adobe_adept", "kobo_kdrm"})


def _trace_enabled() -> bool:
    try:
        # The Core owns the effective level; 'verbose' must not suffice.
        return HTTPX_TRANSPORT.trace_enabled() is True
    except Exception:
        # An older Core or missing broker must not disclose secrets.
        return False


def _safe_id(value: object) -> str | None:
    return value if type(value) is str and 0 < len(value) <= 256 else None


def _safe_url(value: object) -> str | None:
    # Never coerce arbitrary provider objects using their __str__ methods.
    return value if type(value) is str and 0 < len(value) <= _MAX_URL_CHARS else None


def trace_kobo_descriptor_urls(payload: object, *, product_id: object) -> None:
    """Show every candidate URL from an already authorized descriptor POST.

    Includes ambiguous K3 candidates without choosing any of them or adding
    a content GET. Opaque URLs live only in the user-enabled local trace.
    """
    if not _trace_enabled():
        return
    item = _safe_id(product_id)
    if item is None or type(payload) is not dict:
        return
    acquisition = payload.get("acquisition")
    rows = acquisition.get("candidates") if type(acquisition) is dict else None
    if type(rows) is not list or len(rows) > 50:
        return
    for index, row in enumerate(rows):
        if type(row) is not dict:
            continue
        raw = _safe_url(row.get("url"))
        if raw is None:
            continue
        handling = row.get("handling")
        handling = handling if handling in _HANDLING else "other"
        try:
            _LOG.debug(
                "TRACE kobo private_candidate_url phase=offer "
                "product_id=%r candidate_index=%d handling=%s url=%r",
                item, index, handling, raw,
            )
        except Exception:
            pass


def trace_kobo_http_url(
    phase: object, raw: object, *, product_id: object,
) -> None:
    """Log initial and redirected Core delivery URLs without changing GETs."""
    if not _trace_enabled():
        return
    item, url = _safe_id(product_id), _safe_url(raw)
    if item is None or url is None or phase not in ("initial", "redirect"):
        return
    try:
        _LOG.debug(
            "TRACE kobo private_candidate_url phase=%s product_id=%r url=%r",
            phase, item, url,
        )
    except Exception:
        pass
