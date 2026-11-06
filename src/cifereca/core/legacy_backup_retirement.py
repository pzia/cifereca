"""#3196: transactionally retire ONE redundant v0.7 Kobo backup archive.

This is not a source of positive rights, a provider GET or byte promotion.
The caller must already hold the source acquisition lock and supply an exact,
fresh, provider-observed snapshot with a live context guard. Canonical A/B
publication and its completed delivery journal must independently exist.
Every durable and physical proof is rechecked under one BEGIN IMMEDIATE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Literal

from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from .canonical_publication import _stable
from .legacy_backup_publication_review import review_legacy_backup_publication

_MAX_OBSERVATION_AGE = 900  # seconds; not a reusable user confirmation token
_AUDIT_SCHEMA = "core.legacy-kobo-retirement.v1"


@dataclass(frozen=True, slots=True)
class LegacyKoboRetirement:
    status: Literal["retired", "blocked", "unchanged"]
    reason: str
    changed: bool = False


class _Refused(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Refused(reason)


def _fresh(stamp: str | None) -> bool:
    if type(stamp) is not str:
        return False
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return False
        age = time.time() - parsed.timestamp()
        return -60 <= age <= _MAX_OBSERVATION_AGE
    except (ValueError, TypeError, OverflowError):
        return False


def retire_attested_legacy_kobo_backup(
    storage, *, library_root: Path, snapshot: ProviderRightsSnapshot,
    item_id: str, right_id: str, context_current: Callable[[], bool],
) -> LegacyKoboRetirement:
    """Retire one old row only after fresh revision + exact canonical A/B.

    The writer deletes the redundant compatibility row, never a book, source,
    entitlement, artifact, receipt or placement. An append-only versioned
    audit payload preserves the old 0.7 evidence (including its revision,
    scope and SHA) so deletion does not destroy the only historical pointer.
    The receipt is NOT a permission to use, copy or deliver those bytes.

    This operation is deliberately *not* called by legacy W2: only a trusted
    account-scoped canonical Sync holding its acquisition lock may invoke it.
    The context guard must be side-effect-free and must not reenter Storage.
    """
    blocked = lambda code: LegacyKoboRetirement("blocked", code)
    if (type(snapshot) is not ProviderRightsSnapshot
            or not isinstance(library_root, Path)
            or not library_root.is_absolute()
            or type(item_id) is not str or not item_id
            or type(right_id) is not str or not right_id
            or not callable(context_current)):
        return blocked("invalid_request")
    scope = snapshot.scope
    if (scope.provider_id != "kobo"
            or snapshot.status != "success"
            or snapshot.coverage != "complete"
            or snapshot.pagination_complete is not True
            or not snapshot.snapshot_ref
            or not snapshot.branch
            or not _fresh(snapshot.observed_at)):
        return blocked("current_provider_snapshot_unverified")
    matches = [row for row in snapshot.rights if row.edition_id == item_id]
    if len(matches) != 1:
        return blocked("provider_source_ambiguous")
    observation = matches[0]
    if (observation.right_id != right_id
            or observation.kind != "purchase"
            or observation.state != "active"
            or not observation.provider_attested
            or observation.provider_revision_id is None
            or not _fresh(observation.observed_at)
            or observation.observed_at != snapshot.observed_at):
        return blocked("provider_revision_unverified")
    coords = (
        scope.identity_id, "kobo", scope.credential_id, scope.account_id,
        snapshot.branch, item_id, right_id, "purchase",
    )
    source_id = _stable("source", *coords)
    archive_key = "core.legacy-kobo-retirement." + hashlib.sha256(
        "\x1f".join(coords).encode("utf-8")
    ).hexdigest()
    try:
        if storage.schema_version() < 14:
            return blocked("schema_unavailable")
        if context_current() is not True:
            return blocked("source_context_changed")
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if context_current() is not True:
                return blocked("source_context_changed")
            existing_audit = db.execute(
                "SELECT value FROM metadata WHERE key=?", (archive_key,),
            ).fetchone()
            backup_row = db.execute(
                "SELECT * FROM backup_items WHERE provider_id='kobo' "
                "AND credential_id=? AND item_id=?",
                (scope.credential_id, item_id),
            )
            backup = backup_row.fetchone()
            if backup is None:
                # Idempotent replay, but absence is never physical proof.
                return LegacyKoboRetirement("unchanged", "legacy_archive_absent")
            _require(existing_audit is None, "retirement_journal_conflict")
            keys = [column[0] for column in backup_row.description]
            old = dict(zip(keys, backup))
            _require(
                old.get("identity_id") == scope.identity_id
                and old.get("account_id") == scope.account_id
                and old.get("entitlement_type") == "owned"
                and old.get("status") == "complete"
                and old.get("revision_id") == observation.provider_revision_id,
                "legacy_revision_or_owner_mismatch",
            )
            # The old PK is credential+edition only; a second borrower or
            # branch of that edition is an ambiguity, never a migration hint.
            owners = db.execute(
                "SELECT source_reference_id FROM source_references "
                "WHERE provider_id='kobo' AND credential_id=? "
                "AND edition_id=? LIMIT 2",
                (scope.credential_id, item_id),
            ).fetchall()
            _require(owners == [(source_id,)], "canonical_source_ambiguous")
            current = db.execute(
                "SELECT state,evidence_kind,evidence_ref,observed_at "
                "FROM source_references WHERE source_reference_id=? "
                "AND identity_id=? AND provider_id='kobo' "
                "AND credential_id=? AND account_id=? AND branch_id=? "
                "AND edition_id=? AND right_id=? AND reference_kind='purchase'",
                (source_id, scope.identity_id, scope.credential_id,
                 scope.account_id, snapshot.branch, item_id, right_id),
            ).fetchone()
            _require(current == (
                "active", "provider_response", observation.evidence_ref,
                observation.observed_at,
            ), "canonical_provider_observation_stale")
            # Revalidate all B proof *inside the same write transaction*:
            # OriginLink, exact stage/receipt, physical SHA/inode/profile
            # ownership, and global path collisions. The independent
            # diagnostic itself remains read-only, never a write token.
            review = review_legacy_backup_publication(
                storage, library_root=library_root,
                identity_id=scope.identity_id,
                credential_id=scope.credential_id, item_id=item_id, _db=db,
            )
            _require(
                review.status == "verified_existing_publication",
                "canonical_publication_unverified",
            )
            if context_current() is not True or not _fresh(snapshot.observed_at):
                return blocked("source_context_changed")

            # Record the entire retired compatibility row once. Do not
            # rewrite this journal on replay; SQL INSERT+DELETE are atomic.
            audit = {
                "schema": _AUDIT_SCHEMA, "source": list(coords),
                "provider_snapshot_ref": snapshot.snapshot_ref,
                "provider_evidence_ref": observation.evidence_ref,
                "provider_observed_at": observation.observed_at,
                "legacy_backup": old, "retired_at": int(time.time()),
            }
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (archive_key, json.dumps(audit, ensure_ascii=False,
                                         sort_keys=True)),
            )
            deleted = db.execute(
                "DELETE FROM backup_items WHERE provider_id='kobo' "
                "AND credential_id=? AND item_id=? "
                "AND identity_id=? AND account_id=? AND revision_id=? "
                "AND status='complete'",
                (scope.credential_id, item_id, scope.identity_id,
                 scope.account_id, observation.provider_revision_id),
            )
            _require(deleted.rowcount == 1, "legacy_archive_changed")
        return LegacyKoboRetirement("retired", "exact_canonical_publication", True)
    except _Refused as error:
        return blocked(str(error))
    except (sqlite3.Error, OSError, ValueError, TypeError,
            RuntimeError, RecursionError, OverflowError):
        return blocked("retirement_unavailable")


__all__ = ["LegacyKoboRetirement", "retire_attested_legacy_kobo_backup"]
