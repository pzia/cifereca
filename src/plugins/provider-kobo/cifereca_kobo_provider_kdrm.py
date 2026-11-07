"""Kobo Vault KDRM selected recipe -> public Kobo-Adept request, in memory only.

No ContentKeys, private URL or bearer ever enters a Core receipt, a database
row or diagnostics. This adapter does NOT execute an acquisition.
"""
from __future__ import annotations

import re

from cifereca_kobo_delivery_offer import (
    _safe_https_get, _kdrm_raw_type, effective_kobo_media_format,
)


def _bounded_text(value, limit=8192):
    return (type(value) is str and 0 < len(value) <= limit
            and all(32 <= ord(char) <= 126 for char in value))


def _safe_mapping(value, *, max_items=32, max_value=8192):
    if type(value) is not dict or len(value) > max_items:
        return None
    for key, item in value.items():
        if (not _bounded_text(key, 120)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", key)
                or not _bounded_text(item, max_value)):
            return None
    return dict(value)


def _no_embedded_keys(value):
    """No plugin-supplied ContentKeys, even inside a nested request."""
    if type(value) is dict:
        return all(
            type(k) is str
            and k.casefold().replace("_", "") != "contentkeys"
            and _no_embedded_keys(v)
            for k, v in value.items()
        )
    if type(value) in (list, tuple):
        return len(value) < 128 and all(_no_embedded_keys(x) for x in value)
    return True


def scoped_kdrm_source(delegated, selected, *, product_id, account_id,
                       identity_id, credential_id,
                       kdrm_delegated_ready=False):
    """Return a source.type=kobo recipe only for an exact, replayable source.

    Descriptor candidate was already verified with exact_kobo_offer_id.
    A second, authenticated delegated request supplies the Kobo-only context
    and the key *retrieval recipe*. Neither request nor candidate may contain
    values of the content keys themselves.
    """
    coords = (product_id, account_id, identity_id, credential_id)
    if (any(not _bounded_text(x, 256) for x in coords)
            or type(delegated) is not dict or type(selected) is not dict
            or not _no_embedded_keys(selected)):
        return None
    for container in (delegated, delegated.get("acquisition")):
        if type(container) is not dict:
            return None
        for key, expected in (
            ("account_id", account_id), ("identity_id", identity_id),
            ("credential_id", credential_id), ("product_id", product_id),
        ):
            if key in container and container[key] != expected:
                return None
    acquisition = delegated["acquisition"]
    if acquisition.get("mode") != "delegated" or acquisition.get("product_id") != product_id:
        return None
    delegation = acquisition.get("delegation")
    if type(delegation) is not dict:
        return None
    request = delegation.get("content_access_request")
    context = delegation.get("session_context")
    if (type(request) is not dict or type(context) is not dict
            or not _no_embedded_keys(request)):
        return None
    # An authenticated Kobo source cannot be reconstructed from a delegated
    # envelope that explicitly names another producer. The descriptor already
    # validates provider fields, but the later delegated response is a
    # distinct upstream observation and must be checked independently.
    for container in (delegated, acquisition, delegation, request, context):
        for field in ("provider", "source_provider"):
            if field in container and (
                type(container[field]) is not str or container[field] != "kobo"
            ):
                return None
    # A foreign borrower/product nested in a delegated envelope is just as
    # disqualifying as one at top-level. Never select a matching sibling while
    # replaying a different account's content-key request.
    for container in (delegation, request, context, selected, selected.get("kobo")):
        if container is None:
            continue
        if type(container) is not dict:
            return None
        for key, expected in (
            ("account_id", account_id), ("identity_id", identity_id),
            ("credential_id", credential_id), ("product_id", product_id),
        ):
            if key in container and container[key] != expected:
                return None
    url = request.get("url")
    if (not _safe_https_get({"url": url, "method": request.get("method"),
                             "transport": "https"})
            or request.get("method") != "GET"):
        return None
    request_headers = _safe_mapping(request.get("headers", {}))
    query = _safe_mapping(request.get("query", {}), max_items=32,
                          max_value=2048)
    candidate_headers = _safe_mapping(selected.get("headers", {}))
    device_id = context.get("device_id")
    user_id = context.get("user_id")
    if (request_headers is None or query is None or candidate_headers is None
            or not _bounded_text(device_id, 256)
            or not _bounded_text(user_id, 256)
            or selected.get("kind") != "http"
            or selected.get("handling") != "kobo_kdrm"
            or not _safe_https_get(selected)):
        return None
    kobo = selected.get("kobo")
    if type(kobo) is not dict:
        return None
    selected_source = kobo.get("source")
    # Defensive second guard: the descriptor may have been re-observed or
    # called directly. Its root alias cannot override the executor's
    # nested source without invalidating the selected KDRM recipe.
    if "source" in selected and selected["source"] != selected_source:
        return None
    if selected_source not in {"library_sync", "content_access_book"}:
        return None
    if selected_source == "content_access_book" and kdrm_delegated_ready is not True:
        return None
    drm = selected.get("drm")
    media = effective_kobo_media_format(selected)
    if (type(drm) is not dict or not _kdrm_raw_type(drm.get("raw_type"))
            or media not in ("kepub", "epub")):
        return None
    source_format = selected.get("source_format", media)
    if type(source_format) is str:
        source_format = source_format.strip()
    formats = {("kepub", "kepub"), ("epub", "flepub"), ("epub", "epub")}
    if selected_source == "content_access_book" and kdrm_delegated_ready is True:
        formats |= {("epub", "epub3"), ("epub", "epub3fl")}
    if (type(source_format) is not str
            or (media.casefold(), source_format.casefold()) not in formats):
        return None
    platform = kobo.get("platform", kobo.get("display_profile"))
    if platform is not None and not _bounded_text(platform, 128):
        return None
    if selected_source == "content_access_book":
        # The historic Kobo-Adept delegated ABI replays ContentAccess and
        # selects by exact DRM/URL format. Do not invent a library_sync pin.
        # The delegated envelope must independently reattest the same unique
        # scoped, non-sample KDRM source before handing it to the runtime.
        described = acquisition.get("candidates")
        if type(described) is not list or not 1 <= len(described) <= 50:
            return None
        matches = []
        for row in described:
            if type(row) is not dict:
                return None
            for key, expected in (
                ("product_id", product_id), ("account_id", account_id),
                ("identity_id", identity_id), ("credential_id", credential_id),
            ):
                if key in row and row[key] != expected:
                    return None
            nested = row.get("kobo")
            if type(nested) is not dict or nested.get("source") != "content_access_book":
                continue
            row_drm = row.get("drm")
            if (row.get("handling") == "kobo_kdrm" and row.get("kind") == "http"
                    and type(row_drm) is dict
                    and _kdrm_raw_type(row_drm.get("raw_type"))
                    and row_drm.get("family") == "kobo_kdrm"
                    and row_drm.get("protected") is True
                    and effective_kobo_media_format(row) == media
                    and type(row.get("source_format")) is str
                    and row["source_format"].strip().casefold()
                    == source_format.casefold()
                    and _safe_https_get(row)):
                matches.append(row)
        if len(matches) != 1:
            return None
        return {
            "type": "kobo", "provider": "kobo",
            "request": {
                "url": url, "method": "GET", "headers": request_headers,
                "query": query,
            },
            "selection": {"drm_type": "KDRM", "url_format": source_format},
            "kdrm": {"device_id": device_id, "user_id": user_id},
        }
    pinned = {
        "url": selected["url"], "headers": candidate_headers,
        "drm_type": "KDRM", "media_format": media,
        "source_format": source_format, "delivery_source": "library_sync",
    }
    if platform:
        pinned["platform"] = platform
    return {
        "type": "kobo", "provider": "kobo-vault",
        "request": {
            "url": url, "method": "GET", "headers": request_headers,
            "query": query,
        },
        "candidate": pinned,
        "kdrm": {"device_id": device_id, "user_id": user_id},
    }
