"""#6366/#3724: admitted Search provider image -> Notice C cover attribute.

A provider's already-loaded SearchResult is one parsed, source-exact
observation; it is NOT a permission or a join with another edition.
The Core mirror (not the plugin, GTK or image cache) owns persistence.
No supplier request, entitlement lookup or image fetch is performed here.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from ..models import SearchResult
from .notice_cover_projection import notice_cover_document_ref
from .notice_cover_repository import PersistedNoticeCover, persist_notice_cover
from .notice_source_observation import (
    NoticeSourceKey, NoticeSourceObservation, _id, admit_source_notice,
)
from .search_cover_provenance import project_exact_search_catalogue_cover


def admitted_mirrored_search_cover(
    item: SearchResult, *, identity_id: str,
    qualified_url: str, observed_at: int,
) -> NoticeSourceObservation | None:
    """Admit one direct, already observed source image; never infer absence.

    The producing item has an exact provider, record and upstream docbase.
    Branch identity additionally commits to collection and docbase; one
    supplier's repeated record ID in a different database is NOT identical.
    Edition ID uses the upstream exact record coordinate, not an ISBN/title.
    A provider revision affects the observation, not documentary identity.
    """
    if (type(item) is not SearchResult or not _id(identity_id)
            or not _id(item.source_provider) or not _id(item.record_id)
            or not _id(item.docbase)
            or not _id(item.source_collection, required=False)
            or type(qualified_url) is not str or not qualified_url
            or type(observed_at) is not int or observed_at < 1
            or item.medium not in ("digital", "physical", "unknown")):
        return None
    if (item.revision_id is not None
            and (type(item.revision_id) is not str
                 or len(item.revision_id) > 512
                 or not item.revision_id.isprintable())):
        return None
    metadata = item.metadata
    if (type(metadata) is dict and
            metadata.get("search_provenance") == "known_catalog_stale"):
        return None
    # Reuse the existing Core source/edition admission before retaining any
    # image. This does not make a private catalogue public on URL appearance.
    exact = project_exact_search_catalogue_cover(
        item, identity_id=identity_id, share_public=True,
    )
    if exact is None:
        return None
    public = exact.scope.kind == "public_catalogue"
    if not public and not _id(item.source_credential):
        return None
    if not _id(item.source_account_id, required=False):
        # In particular, a known empty borrower ID and an unknown account
        # cannot silently be collapsed into the same private Notice C source.
        return None
    branch = "search:" + sha256(json.dumps(
        (item.docbase, item.source_collection),
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    key = NoticeSourceKey(
        provider_id=item.source_provider,
        visibility="public" if public else "private",
        identity_id=None if public else identity_id,
        credential_id=None if public else item.source_credential,
        account_id=None if public else item.source_account_id,
        branch_id=branch, record_id=item.record_id,
        edition_id=item.record_id,
        manifestation_role="print" if item.medium == "physical" else item.medium,
    )
    fields: dict[str, Any] = {"media": {"cover": qualified_url}}
    if item.revision_id is not None:
        # Do not copy opaque provider revision tokens into descriptive C;
        # only their fingerprint is needed for source-change invalidation.
        fields["revision_fingerprint"] = sha256(
            item.revision_id.encode("utf-8"),
        ).hexdigest()
    try:
        return admit_source_notice(
            key=key, parser_id="core-search-provider-result",
            parser_version="1", observed_at=observed_at,
            raw_fields=fields, status="complete",
        )
    except (TypeError, ValueError, UnicodeError, OverflowError):
        # Malformed/signed URLs and malformed source coordinates are *not*
        # missing-cover facts. Keep the legacy descriptive mirror untouched.
        return None


def persist_mirrored_search_cover(
    storage: Any, item: SearchResult, *, identity_id: str,
    qualified_url: str, observed_at: int,
) -> PersistedNoticeCover | None:
    """Write the admitted field via Core's existing Notice C metadata store.

    A publisher with no admissible cover does NOT assert an empty inventory,
    even when its full SearchResult was received. Missing must be proved by
    the source parser separately.
    """
    observation = admitted_mirrored_search_cover(
        item, identity_id=identity_id, qualified_url=qualified_url,
        observed_at=observed_at,
    )
    if observation is None:
        return None
    saved = persist_notice_cover(
        storage, observation, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    if saved.document != notice_cover_document_ref(observation.key):
        raise ValueError("notice_c_search_source_mismatch")
    return saved


__all__ = ["admitted_mirrored_search_cover", "persist_mirrored_search_cover"]
