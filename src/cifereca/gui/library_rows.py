"""GTK-free Library row projection, with business decisions injected (#1129).

The Core retains ownership of identities, permissions, acquisition state and
the library snapshot. Rendering must never rescan or fetch a provider.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ..core.search import local_match_explanation


def _received_local_file_label(item: dict[str, Any]) -> str | None:
    """Show file origin without interpreting a copy as a recipient entitlement.

    Transfer provenance is a recorded local fact, not proof of an upstream
    purchase in B's account. A separate owned entitlement, if any, remains an
    independent library fact.
    """
    if item.get("local") is not True:
        return None
    meta = item.get("metadata")
    if type(meta) is not dict or meta.get("transfer_kind") != "local_copy":
        return None
    origin = meta.get("file_origin_identity_id")
    owner = item.get("identity_id")
    source_placement = meta.get("materialized_from")
    if (type(origin) is not str or not 0 < len(origin) <= 128
            or type(owner) is not str or not owner or origin == owner
            or type(source_placement) is not str or not source_placement):
        return None
    return f"Fichier reçu de {origin} · aucun droit transmis"



def _recipient_independent_kobo_right_label(item: dict[str, Any]) -> str | None:
    """Label B's *independent* purchase, never adoption of A's received file.

    Library's normalized possessions are positive entitlement projections.
    An active, exact Kobo product right scoped to the receiving identity and
    its own account/entitlement is a fact separate from A's local transfer.
    The row does not contain a confirmed backup receipt: do not say linked.
    """
    if (_received_local_file_label(item) is None
            or item.get("provider_id") != "kobo"):
        return None
    owner = item.get("identity_id")
    product = item.get("item_id") or item.get("source_item_id")
    if (type(owner) is not str or not owner
            or type(product) is not str or not product):
        return None
    possessions = item.get("possessions")
    if type(possessions) not in (list, tuple) or len(possessions) > 256:
        return None
    matches = []
    for right in possessions:
        if (type(right) is dict
                and right.get("identity_id") == owner
                and right.get("provider_id") == "kobo"
                and right.get("item_id") == product
                and right.get("kind") == "owned"
                and right.get("state") == "active"
                and not right.get("expires_at")
                and type(right.get("account_id")) is str
                and bool(right["account_id"])
                and type(right.get("entitlement_id")) is str
                and bool(right["entitlement_id"])):
            matches.append(right)
    # Conflicting accounts or multiple different purchases are an ambiguous
    # row, not an excuse to silently choose the owner of the received bytes.
    if len(matches) != 1:
        return None
    return f"Achat Kobo indépendant pour {owner}"


def project_library_rows(
    filtered_entries: Iterable[dict[str, Any]], *,
    row_factory: Callable[..., Any],
    provider_display_name: Callable[[str], str],
    availability_label: Callable[[dict[str, Any]], str],
    backup_status_label: Callable[[str], str],
    format_date: Callable[[str], str],
    query: str = "",
    field: str = "all",
) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Project the already-filtered snapshot, preserving keys and display order."""
    rows: list[Any] = []
    objects: dict[str, dict[str, Any]] = {}
    for item in filtered_entries:
        provider = str(item.get("provider_id") or "local")
        providers = list(item.get("providers") or [provider])
        provider_label = (
            f"{len(providers)} sources" if len(providers) > 1
            else provider_display_name(provider)
        )
        state = availability_label(item)
        key = str(item.get("key") or f"library:{len(objects)}")
        objects[key] = item
        meta_parts = [state] if state else []
        received = _received_local_file_label(item)
        if received:
            meta_parts.append(received)
            own_right = _recipient_independent_kobo_right_label(item)
            if own_right:
                meta_parts.append(own_right)
        match_reason = local_match_explanation(item, query, field=field)
        if match_reason:
            meta_parts.append(match_reason)
        # A store entitlement may have an expiry/refresh timestamp, but it
        # is never a library return deadline. Render "retour" only when
        # the row carries explicit loan provenance, not for owned rights.
        is_loan = (
            item.get("source") == "loan"
            or item.get("entitlement_type") == "loan"
            or item.get("state") in {"borrowed", "remote_loan_active", "remote_loan_realized"}
        )
        if is_loan and item.get("due_at"):
            meta_parts.append(f"retour {format_date(str(item.get('due_at')))}")
        sync_status = item.get("sync_status")
        if sync_status and not item.get("local"):
            meta_parts.append(backup_status_label(str(sync_status)))
        device_statuses = (
            item.get("device_statuses")
            if isinstance(item.get("device_statuses"), list) else []
        )
        if device_statuses:
            present_count = sum(
                1 for row in device_statuses
                if row.get("observed_state") == "present"
            )
            keep_count = sum(
                1 for row in device_statuses
                if row.get("desired_state") == "keep"
            )
            if present_count:
                meta_parts.append(f"Kobo {present_count}/{len(device_statuses)}")
            elif keep_count:
                meta_parts.append("Kobo à synchroniser")
        rows.append(row_factory(
            key, str(item.get("title") or "Sans titre"),
            str(item.get("author") or ""),
            " · ".join(part for part in meta_parts if part),
            provider_label, item.get("thumbnail_url"),
        ))
    return rows, objects
