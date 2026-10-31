"""#3185: private, read-only review of legacy provider-bound physical placements.

A pre-v14 Placement stores provider and item, but not credential or borrower.
Even one matching SourceReference/OriginLink on its shared Artifact cannot
establish ownership of that particular file. This diagnostic never migrates
the Placement, grants a right or authorizes an unlink.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Literal

from .canonical_post_placement import verified_post_placement_duplicates

_MAX_ORIGINS = 256
_MAX_SCOPES = 64


@dataclass(frozen=True, slots=True)
class LegacyPlacementScopeReview:
    status: Literal["blocked"]
    reason: str
    candidate_placement_id: str
    keeper_placement_id: str
    # Historical provider origins are only Artifact/identity facts.
    # They are NEVER associated with the candidate file by this review.
    matching_provider_origin_links: int = 0
    matching_borrower_scopes: int = 0
    physical_pair_verified_at_review: bool = False

    @property
    def may_migrate_or_retire(self) -> bool:
        return False


def review_legacy_placement_scope(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str,
) -> LegacyPlacementScopeReview:
    """Classify an old provider/item Placement against a verified canonical B.

    Closed reasons do not reveal credentials, paths, edition IDs, account IDs
    or receipts. A different profile, absent bytes or ambiguous scope blocks
    the review without guessing which account owned the old physical path.
    """
    def blocked(reason, *, links=0, scopes=0, verified=False):
        return LegacyPlacementScopeReview(
            "blocked", reason, candidate_placement_id,
            keeper_placement_id, links, scopes, verified,
        )

    if (not isinstance(library_root, Path)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id):
        return blocked("invalid_request")
    try:
        if storage.schema_version() < 14:
            return blocked("schema_unavailable")
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            query = (
                "SELECT path,identity_id,artifact_id,state,provider_id,"
                "source_item_id FROM placements WHERE placement_id=?"
            )
            candidate = db.execute(query, (candidate_placement_id,)).fetchone()
            keeper = db.execute(query, (keeper_placement_id,)).fetchone()
            if candidate is None or keeper is None:
                return blocked("placement_missing")
            if (candidate[1:4] != keeper[1:4]
                    or candidate[3] != "present"
                    or candidate[0] == keeper[0]
                    or type(candidate[1]) is not str or not candidate[1]
                    or type(candidate[2]) is not str
                    or type(candidate[0]) is not str or not candidate[0]):
                return blocked("scope_mismatch")
            if keeper[4:] != (None, None):
                return blocked("keeper_not_canonical")
            if candidate[4:] == (None, None):
                return blocked("not_legacy")
            if (type(candidate[4]) is not str or not candidate[4]
                    or type(candidate[5]) is not str or not candidate[5]):
                return blocked("legacy_scope_incomplete")

        # Hash *both* paths inside the recorded unique profile. A shared
        # SQLite Artifact ID alone is insufficient, and no provider GET occurs.
        candidates = verified_post_placement_duplicates(
            storage, library_root=library_root,
            placement_id=keeper_placement_id,
        )
        if candidate_placement_id not in candidates:
            return blocked("physical_unverified")

        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            # No old file is ever associated with a borrower on provider+item
            # alone. Recheck the exact placement rows after hashing, then
            # inspect only published provider origins for this Artifact/profile.
            if (db.execute(query, (candidate_placement_id,)).fetchone()
                    != candidate
                    or db.execute(query, (keeper_placement_id,)).fetchone()
                    != keeper):
                return blocked("scope_changed")
            links = db.execute(
                "SELECT s.credential_id,s.account_id "
                "FROM origin_links o JOIN source_references s "
                "ON s.source_reference_id=o.source_reference_id "
                "WHERE o.origin_kind='provider' AND o.identity_id=? "
                "AND o.artifact_id=? AND s.identity_id=? "
                "AND s.provider_id=? AND s.edition_id=? "
                "AND s.evidence_kind='provider_response' "
                "AND typeof(o.evidence_ref)='text' AND o.evidence_ref!='' "
                "LIMIT ?",
                (candidate[1], candidate[2], candidate[1],
                 candidate[4], candidate[5], _MAX_ORIGINS + 1),
            ).fetchall()
            if len(links) > _MAX_ORIGINS:
                return blocked("origin_budget_exceeded", verified=True)
            scopes = set()
            for credential, account in links:
                if (type(credential) is not str or not credential
                        or type(account) is not str):
                    return blocked("origin_scope_incomplete", verified=True)
                scopes.add((credential, account))
            if len(scopes) > _MAX_SCOPES:
                return blocked("origin_budget_exceeded", verified=True)
            if not links:
                return blocked("origin_unverified", verified=True)
            if len(scopes) > 1:
                return blocked(
                    "multiple_borrower_scopes",
                    links=len(links), scopes=len(scopes), verified=True,
                )
            # A unique *recorded* source scope still does not prove which
            # particular Placement carried its account/receipt historically.
            return blocked(
                "legacy_file_source_unbound",
                links=len(links), scopes=1, verified=True,
            )
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError):
        return blocked("inspection_unavailable")
    except Exception:
        # Unreadable storage or a changed profile is not proof of provenance.
        return blocked("inspection_unavailable")


__all__ = ["LegacyPlacementScopeReview", "review_legacy_placement_scope"]
