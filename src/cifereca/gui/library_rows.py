"""GTK-free Library row projection, with business decisions injected (#1129).

The Core retains ownership of identities, permissions, acquisition state and
the library snapshot. Rendering must never rescan or fetch a provider.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from ..core.search import local_match_explanation
from ..core.local_cover_evidence import VerifiedLocalCoverEvidence
from ..core.notice_c_local_origin_projection import valid_local_public_c_link
from ..core.notice_c_purchase_library_projection import purchase_library_notice


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



def _recipient_independent_owned_right_label(
    item: dict[str, Any], provider_display_name: Callable[[str], str],
) -> str | None:
    """Label B's *independent* purchase, never adoption of A's received file.

    Library's normalized possessions are positive entitlement projections.
    An active, exact product right scoped to the receiving identity and
    its own account/entitlement is a fact separate from A's local transfer.
    The row does not contain a confirmed backup receipt: do not say linked.
    """
    if _received_local_file_label(item) is None:
        return None
    provider_id = item.get("provider_id")
    if type(provider_id) is not str or not provider_id or len(provider_id) > 64:
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
                and right.get("provider_id") == provider_id
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
    provider_label = provider_display_name(provider_id)
    if type(provider_label) is not str or not provider_label or len(provider_label) > 80:
        return None
    return f"Achat {provider_label} indépendant pour {owner}"


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
    # Keys are also the lookup identifiers for the detail/action model.
    # Two different Library items with the same GTK key must not borrow
    # one another's documentary cover or select an arbitrary last-writer.
    # Preserve the old library:N fallback based on unique mapped keys.
    source_items = list(filtered_entries)
    display_keys: list[str] = []
    seen_keys: set[str] = set()
    for item in source_items:
        ui_key = item.get("_library_ui_key")
        key = (
            ui_key if type(ui_key) is str and 0 < len(ui_key) <= 1300
            else str(item.get("key") or f"library:{len(seen_keys)}")
        )
        display_keys.append(key)
        seen_keys.add(key)
    ambiguous_keys = {
        key for key, count in Counter(display_keys).items() if count > 1
    }
    rows: list[Any] = []
    objects: dict[str, dict[str, Any]] = {}
    for item, key in zip(source_items, display_keys):
        provider = str(item.get("provider_id") or "local")
        providers = list(item.get("providers") or [provider])
        provider_label = (
            f"{len(providers)} sources" if len(providers) > 1
            else provider_display_name(provider)
        )
        state = availability_label(item)
        # The global read view can show the same canonical book key under
        # separate profile owners. Keep Core's book key unchanged for actions;
        # only the GTK list/mapping key is namespaced by the read adapter.
        objects[key] = item
        meta_parts = [state] if state else []
        owner = item.get("_library_view_owner")
        if type(owner) is str and 0 < len(owner) <= 160:
            meta_parts.append("Profil : " + owner)
        received = _received_local_file_label(item)
        if received:
            meta_parts.append(received)
            own_right = _recipient_independent_owned_right_label(item, provider_display_name)
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
                meta_parts.append(f"Appareil {present_count}/{len(device_statuses)}")
            elif keep_count:
                meta_parts.append("Appareil à synchroniser")
        title = str(item.get("title") or "Sans titre")
        author = str(item.get("author") or "")
        documentary = (purchase_library_notice(item) if key not in ambiguous_keys else None)
        if documentary is not None and not author.strip():
            public_author = next((fact.value for fact in documentary.notice.facts
                                  if fact.field == "author"), "")
            if public_author:
                author = public_author
                meta_parts.append("Auteur : catalogue public")
        meta = " · ".join(part for part in meta_parts if part)
        # A rejected Core packet cannot silently downgrade to the
        # descriptive old URL. Only an unassociated legacy row may retain
        # that compatibility path; an attested row is N2-first.
        link = item.get("_public_canonical_cover_link")
        safe_link = (
            key not in ambiguous_keys
            and item.get("_exact_local_cover_evidence") is None
            and valid_local_public_c_link(
                link, identity_id=item.get("identity_id"),
                artifact_id=item.get("artifact_id"),
                placement_id=item.get("placement_id"),
            )
            and item.get("_library_view_owner") in (
                None, item.get("identity_id"),
            )
        )
        cover_url = (
            item.get("thumbnail_url")
            if key not in ambiguous_keys
            and item.get("_exact_local_cover_evidence") is None
            and link is None else None
        )
        row = row_factory(key, title, author, meta, provider_label, cover_url)
        # MasterDetailPage.show_rows preserves same-key rows when presentation
        # signatures match. A later canonical Library snapshot may change the
        # cover without changing its Artifact/SourceKey: force the mapped GTK
        # cell to rebind only when its actual presentation or owner changed.
        # Keep the GTK-free projector compatible with tuple factories used by
        # legacy headless adapters and tests.
        if hasattr(row, "__dict__"):
            if key in ambiguous_keys:
                row._library_ambiguous_key = True
            row._public_row_content = (
                "library", key, title, author, meta, provider_label, cover_url,
                item.get("identity_id"), item.get("_library_view_owner"),
                item.get("provider_id"), item.get("item_id"),
            )
            if documentary is not None:
                row._purchase_library_notice = documentary
                row._public_row_content += (documentary,)
            if key in ambiguous_keys:
                # Force a GTK rebind even when the visible fields are equal
                # to an earlier, once-unique selected row.
                row._public_row_content += ("ambiguous-library-key",)
            # Attach ONLY the private, owner-scoped Core proof created after
            # local_book_path() validated the exact Artifact+Placement. Never
            # infer a document association from image URL/title/ISBN/SHA.
            facts = item.get("_exact_local_cover_evidence")
            if (key not in ambiguous_keys
                    and type(facts) is VerifiedLocalCoverEvidence
                    and facts.scope.identity_id == item.get("identity_id")
                    and facts.scope.artifact_id == item.get("artifact_id")
                    and facts.scope.placement_id == item.get("placement_id")
                    and facts.scope.source_key == item.get("key")
                    and not (
                        item.get("_library_view_owner") is not None
                        and item.get("_library_view_owner") != item.get("identity_id")
                    )):
                row._cover_local_evidence = facts
                row._cover_document_ref = facts.scope
                # A changed exact source must rebind an existing GTK row even
                # if the visible thumbnail_url is unchanged (N3 still hot).
                row._public_row_content = (*row._public_row_content, facts)
            elif safe_link:
                # This is a Core-vetted public catalogue fallback, not a
                # different private Placement and never an N3 URL shortcut.
                row._cover_public_c_link = link
                row._cover_document_ref = link.document
                row._public_row_content = (
                    *row._public_row_content, link.document, link.epoch,
                )
        rows.append(row)
    return rows, objects
