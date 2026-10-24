"""Project attested Kobo personal-manifest ownership into durable SQLite rights.

This is a projection of an already authenticated, explicit session.library()
result. It never queries Kobo, resolves an acquisition, downloads or revokes a
right based on absent rows (manifests may be partial/cached).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..acquisition_engine import Entitlement, SourceIdentity, stable_id
from ..models import SearchResult


def mirror_kobo_manifest_rights(
    service: Any, rows: Iterable[SearchResult], *, identity_id: str,
    credential_id: str, account_id: str | None,
) -> int:
    """Persist only positive ownership *from a Kobo personal library*.

    The manifest caller must be a connected Kobo session tied to the selected
    identity. A catalog SearchResult elsewhere is NOT a valid ownership proof.
    Missing items are never revoked: freshness/completeness is not established.
    """
    if not identity_id or not credential_id:
        return 0
    account_id = str(account_id or "") or None
    existing = service.storage.entitlements(provider_id="kobo")
    by_scope = {
        (
            str(item.get("identity_id") or ""),
            str(item.get("credential_id") or ""),
            str(item.get("account_id") or "") or None,
            str(item.get("item_id") or ""),
        ): item
        for item in existing
        if item.get("entitlement_type") == "owned"
    }
    used_ids = {str(item.get("entitlement_id") or ""): str(item.get("identity_id") or "") for item in existing}
    count = 0
    for row in rows:
        if not isinstance(row, SearchResult):
            continue
        if (
            row.source_provider != "kobo"
            or row.source_credential != credential_id
            or row.owned is not True
            or not isinstance(row.record_id, str)
            or not row.record_id.strip()
        ):
            continue
        item_id = row.record_id.strip()
        key = (identity_id, credential_id, account_id, item_id)
        previous = by_scope.get(key)
        source = SourceIdentity("kobo", credential_id, account_id, identity_id)
        canonical = Entitlement(source=source, item_id=item_id).entitlement_id
        # Storage's legacy entitlement ID does not include identity. Preserve
        # it when unique, but NEVER upsert over another identity's grant.
        ident = (
            str(previous["entitlement_id"]) if previous else
            canonical if canonical not in used_ids or used_ids[canonical] == identity_id
            else stable_id("kobo-manifest-identity", identity_id, credential_id, account_id, item_id)
        )
        metadata = row.metadata if isinstance(row.metadata, dict) else {}
        archived = metadata.get("archived") is True
        # A discovery-time reservation is not an atomic owner proof.
        # Only the guarded SQLite UPSERT may refresh an existing purchase.
        owned_row = {
            "entitlement_id": ident,
            "provider_id": "kobo", "credential_id": credential_id,
            "account_id": account_id, "identity_id": identity_id,
            "item_id": item_id, "revision_id": row.revision_id,
            "entitlement_type": "owned",
            "state": "archived" if archived else "active",
            "metadata": {
                "title": str(row.title or item_id)[:300],
                "author": str(row.author)[:300] if row.author else None,
                "media_format": row.format,
                "cover_url": row.thumbnail_url,
                "archived": archived,
                "downloadable": row.downloadable is True,
                "source": "kobo.personal_manifest",
            },
        }
        # Discovery and commit are separate: a competing identity can
        # claim the canonical ID between the read and our UPSERT. Use the
        # scoped fallback, but never overwrite a collided fallback either.
        if not service.storage.entitlement_put_attested_owned(owned_row):
            if previous is not None or ident != canonical:
                continue
            fallback = stable_id(
                "kobo-manifest-identity", identity_id,
                credential_id, account_id, item_id,
            )
            owned_row["entitlement_id"] = fallback
            if not service.storage.entitlement_put_attested_owned(owned_row):
                continue
            ident = fallback
        by_scope[key] = {"entitlement_id": ident}
        used_ids[ident] = identity_id
        count += 1
    return count


def kobo_personal_right_states(service: Any, identity_id: str) -> dict[tuple[str, str], str]:
    """Read only attested Kobo rights for Search's current identity.

    Public catalog owned flags are not entitlement evidence; keep archived
    purchases distinct. An active right wins over an archived duplicate.
    """
    if not identity_id:
        return {}
    states: dict[tuple[str, str], str] = {}
    for right in service.storage.entitlements(provider_id="kobo"):
        if (
            right.get("identity_id") != identity_id
            or right.get("entitlement_type") != "owned"
            or right.get("state") not in {"active", "archived"}
        ):
            continue
        credential = str(right.get("credential_id") or "")
        item_id = str(right.get("item_id") or "")
        if not credential or not item_id:
            continue
        key = (credential, item_id)
        if key not in states or right["state"] == "active":
            states[key] = right["state"]
    return states
