"""P0 #5073: provider-neutral purchase evidence for already-loaded Search rows.

The only authority is Core's active, identity-scoped canonical entitlement
ledger. This read-only snapshot is built once before GTK rendering; no
provider, account session, HTTP or acquisition action is called here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import SearchResult

_MAX_RIGHTS = 100_000
_MAX_COORD = 512


@dataclass(frozen=True, slots=True)
class SearchOwnedEvidence:
    """Immutable Core-built canonical purchase scope, never a provider flag."""
    identity_id: str
    rights: frozenset[tuple[str, str, str, str, str]]
    account_context_epoch: int | None = None
    source_context_epoch: int | None = None
    schema_version: int = 1


def _coordinate(value: object, *, allow_empty: bool = False) -> bool:
    return (
        type(value) is str and len(value) <= _MAX_COORD
        and (bool(value) or allow_empty)
        and value == value.strip()
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    )


def snapshot_search_owned_evidence(service: Any) -> SearchOwnedEvidence | None:
    """Atomically project only known active owned rights, or refuse entirely.

    A missing, malformed or excessive ledger yields unknown, not a partial
    purchase proof. Caller must not cache snapshots across identity changes.
    """
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    reader = getattr(getattr(service, "storage", None), "entitlements", None)
    if not _coordinate(identity) or not callable(reader):
        return None
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    if any(type(value) not in (int, type(None)) or (type(value) is int and value < 0)
           for value in (account_epoch, source_epoch)):
        return None
    try:
        rows = reader(state="active")
        if type(rows) is not list or len(rows) > _MAX_RIGHTS:
            return None
        rights: set[tuple[str, str, str, str, str]] = set()
        for row in rows:
            if type(row) is not dict:
                return None
            if (row.get("state") != "active"
                    or row.get("entitlement_type") != "owned"
                    or row.get("identity_id") != identity):
                continue
            keys = (
                row.get("provider_id"), row.get("credential_id"),
                row.get("account_id"), row.get("item_id"),
            )
            if not all(_coordinate(value) for value in keys):
                continue
            revision = row.get("revision_id")
            if revision is not None and not _coordinate(revision):
                continue
            rights.add((*keys, revision or ""))
        if (getattr(getattr(service, "current_identity", None), "identity_id", None) != identity
                or getattr(service, "_account_context_epoch", None) != account_epoch
                or getattr(service, "_source_context_epoch", None) != source_epoch):
            return None
        return SearchOwnedEvidence(identity, frozenset(rights), account_epoch, source_epoch)
    except Exception:
        return None


def search_result_has_verified_purchase(
    item: SearchResult, evidence: SearchOwnedEvidence | None, *,
    current_identity_id: object,
    current_account_epoch: object = None,
    current_source_epoch: object = None,
) -> bool:
    """No provider-controlled flag, scenario or metadata can create a right."""
    if (type(item) is not SearchResult or type(evidence) is not SearchOwnedEvidence
            or not _coordinate(current_identity_id)
            or evidence.identity_id != current_identity_id
            or type(evidence.schema_version) is not int or evidence.schema_version != 1
            or (evidence.account_context_epoch is not None
                and evidence.account_context_epoch != current_account_epoch)
            or (evidence.source_context_epoch is not None
                and evidence.source_context_epoch != current_source_epoch)
            or (type(item.metadata) is dict
                and item.metadata.get("search_provenance") == "known_catalog_stale")):
        return False
    keys = (
        item.source_provider, item.source_credential,
        item.source_account_id, item.record_id,
    )
    if not all(_coordinate(value) for value in keys):
        return False
    # Negative guards only: a public/commercial notice cannot borrow a
    # private right merely by echoing exact coordinates. These legacy labels
    # never provide positive authority; the canonical ledger does.
    if (item.source_credential == "__public__"
            or item.source_collection in {
                "catalog", "public_catalog", "commercial_catalog",
            }):
        return False
    revision = item.revision_id
    if revision is not None and not _coordinate(revision):
        return False
    return (*keys, revision or "") in evidence.rights
