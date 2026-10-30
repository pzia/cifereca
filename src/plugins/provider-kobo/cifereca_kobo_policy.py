"""Kobo provider-owned, strictly advisory personal-manifest policy (#1346).

No network, credentials, Core storage, filesystem, download URL, or DRM logic
is available here. Only a fresh account-scoped Core operation may ultimately
decide whether a file can be materialized. The policy cannot grant that right.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

_MAX_ITEMS = 4096
_MAX_COUNT = 1_000_000
_CATEGORIES = (
    "invalid_shape",
    "missing_product_id",
    "invalid_permission_flags",
    "different_account",
    "duplicate_product_id",
    "archived_book",
    "not_downloadable",
    "blocked_scope",
    "candidate_unverified",
)
_SCOPE_DENIED = frozenset({"denied", "forbidden", "requires_scope", "disabled", "unavailable"})


def _strict_product_id(item: dict[str, Any]) -> str | None:
    """Return one unambiguous edition/product key, never a coerced fallback.

    Kobo-Vault may emit the legacy `id` alias and/or `product_id`. If
    both are supplied, they must attest the *same* product. A false, numeric
    or malformed primary must not be replaced with a valid-looking alias;
    such a row could otherwise be counted as a safe, unique candidate.
    """
    values: list[str] = []
    for key in ("product_id", "id"):
        if key not in item or item[key] is None or item[key] == "":
            continue
        value = item[key]
        if (
            type(value) is not str or not 1 <= len(value) <= 256
            or value != value.strip()
            or any(not char.isprintable() or char.isspace() for char in value)
        ):
            return None
        values.append(value)
    if not values or any(value != values[0] for value in values[1:]):
        return None
    return values[0]


def _strict_account_scope(account_id: object) -> bool:
    """Mirror the provider's attested /account ID envelope for direct callers."""
    return (
        type(account_id) is str and 1 <= len(account_id) <= 256
        and all(33 <= ord(char) <= 126 for char in account_id)
    )


def _blank_policy(state: str) -> dict[str, Any]:
    return {
        "schema": "kobo.manifest.materialization.policy/1",
        "phase": "local_materialization",
        "status": state,
        "counts": {key: 0 for key in _CATEGORIES},
        "classified_items": 0,
        "manifest_rejections": 0,
        "truncated": False,
        "manifest_authoritative": False,
        "requires_core_reconciliation": True,
        "acquisition_authorized": False,
        "downloads_authorized": False,
        "deletion_authorized": False,
    }


def classify_personal_manifest(
    rows: object, *,
    account_id: object,
    rejection_count: object = 0,
    acquisition_access: object = "unknown",
    delegation_forbidden: object = False,
) -> dict[str, Any]:
    """Classify only public facts from an authenticated *one-account* manifest.

    Omitted booleans are accepted solely as legacy *personal library* facts;
    they do not imply a downloadable file. No individual record, product ID,
    upstream message or rejected JSON value is ever returned.

    The result is *not* a download plan. It deliberately cannot report
    'safe to acquire' until Core independently proves ledger, placement,
    exact delivery capability, account binding, consent, safety guards and\n    idempotence for each item. Right-state metadata is optional.
    """
    result = _blank_policy("invalid_scope")
    if not _strict_account_scope(account_id):
        return result
    if type(rows) not in (list, tuple):
        result["status"] = "invalid_manifest"
        return result
    if type(rejection_count) is not int or not 0 <= rejection_count <= _MAX_COUNT:
        result["status"] = "invalid_manifest"
        return result
    if type(delegation_forbidden) is not bool:
        result["status"] = "invalid_scope"
        return result

    result["manifest_rejections"] = rejection_count
    result["truncated"] = len(rows) > _MAX_ITEMS
    source = rows[:_MAX_ITEMS]
    product_ids = Counter()
    for item in source:
        if type(item) is not dict:
            continue
        product = _strict_product_id(item)
        if product is not None:
            product_ids[product] += 1

    scope_blocked = (
        delegation_forbidden is True
        or acquisition_access in _SCOPE_DENIED
        if type(acquisition_access) is str
        else delegation_forbidden is True
    )

    for item in source:
        category = None
        if type(item) is not dict:
            category = "invalid_shape"
        else:
            product = _strict_product_id(item)
            if product is None:
                # Preserve the public v1 category vocabulary: contradictory
                # identifiers are equivalent to lacking reliable provenance.
                category = "missing_product_id"
            elif any(
                key in item and item[key] is not None and type(item[key]) is not bool
                for key in ("owned", "downloadable", "archived")
            ) or item.get("owned") is False:
                category = "invalid_permission_flags"
            elif (
                "account_id" in item and
                (type(item["account_id"]) is not str or item["account_id"] != account_id)
            ):
                category = "different_account"
            elif product_ids[product] != 1:
                # Revisions for the same product are ambiguous without
                # a Core ledger/revision decision; do not prefer first/last.
                category = "duplicate_product_id"
            elif item.get("archived") is True:
                category = "archived_book"
            elif item.get("downloadable") is False:
                category = "not_downloadable"
            elif scope_blocked:
                category = "blocked_scope"
            else:
                category = "candidate_unverified"
        result["counts"][category] += 1
        result["classified_items"] += 1

    result["manifest_authoritative"] = (
        not result["truncated"]
        and rejection_count == 0
        and not any(result["counts"][key] for key in (
            "invalid_shape", "missing_product_id", "invalid_permission_flags",
            "different_account", "duplicate_product_id",
        ))
    )
    result["status"] = (
        "incomplete" if not result["manifest_authoritative"]
        else "blocked_scope" if scope_blocked
        else "classified"
    )
    return result


__all__ = ["classify_personal_manifest"]
