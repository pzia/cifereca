"""#3909: recover historically *committed* Core staging placements, with proof.

Only the already-admitted source Sync may invoke this, under its exclusive
acquisition lock. No unlinked provider temp file, matching title, shared SHA,
or another profile may become a canonical purchase by this repair.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Callable

from ..catalog import LocalCatalog
from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_publication import _same_file
from .canonical_promotion import (
    _profile, _stage_parts, cleanup_published_staging, promote_completed_delivery,
)
from .canonical_recovery import (
    _exact_borrower_bound_in_db, _path_verified, _scope, _stage_id,
    _unique_checkpoint_fields,
)
from .source_convergence import SourceKey

_LOG = logging.getLogger("cifereca")
# A damaged/imported SQLite may contain arbitrarily many claims to one A.
# Bound the receipt comparison *before* loading rows into Python.
_MAX_EXACT_ORIGIN_ROWS = 512


def _record(db: sqlite3.Connection, source: SourceKey, placement_id: str):
    """All evidence is joined on exact identity, source, artifact and receipt."""
    scope = source.scope
    row = db.execute(
        "SELECT p.path,p.identity_id,p.artifact_id,p.state,a.sha256,a.size,"
        "p.metadata_json FROM placements AS p "
        "JOIN artifacts AS a ON a.artifact_id=p.artifact_id "
        "WHERE p.placement_id=?", (placement_id,),
    ).fetchone()
    if (row is None or row[1] != scope.identity_id or row[3] != "present"
            or type(row[0]) is not str or not row[0]
            or type(row[4]) is not str or len(row[4]) != 64
            or row[2] != "sha256:" + row[4]
            or type(row[5]) is not int or row[5] <= 0):
        raise ValueError("canonical_stock_placement_unverified")
    # The caller already owns the Core Storage transaction. Reuse the exact,
    # bounded connection+borrower proof from the completed-delivery journal:
    # an unrelated account roster or a stale foreign connection is not an
    # authorization to promote historical staging bytes.
    if not _exact_borrower_bound_in_db(db, scope):
        raise ValueError("canonical_stock_account_unbound")
    references = db.execute(
        "SELECT o.evidence_ref FROM origin_links AS o "
        "JOIN source_references AS s "
        "ON s.source_reference_id=o.source_reference_id "
        "WHERE o.artifact_id=? AND o.identity_id=? "
        "AND o.origin_kind='provider' "
        "AND s.identity_id=? AND s.provider_id=? AND s.credential_id=? "
        "AND s.account_id=? AND s.branch_id=? "
        "AND s.edition_id=? AND s.right_id=? "
        "AND s.reference_kind=? AND s.evidence_kind='provider_response' "
        "AND s.evidence_ref<>'' "
        "ORDER BY o.origin_link_id LIMIT ?",
        (row[2], scope.identity_id, scope.identity_id, scope.provider_id,
         scope.credential_id, scope.account_id, source.branch,
         source.edition_id, source.right_id, source.kind,
         _MAX_EXACT_ORIGIN_ROWS + 1),
    ).fetchall()
    if len(references) > _MAX_EXACT_ORIGIN_ROWS:
        # A truncated link set might exclude the only genuine receipt.
        # Do not pick an arbitrary one or promote unbounded provenance.
        raise ValueError("canonical_stock_receipt_missing_or_ambiguous")
    if not references:
        raise ValueError("canonical_stock_exact_origin_missing")

    # Historical canonical receipts are the independent, per-source proof of
    # delivery; origin by SHA alone cannot bind a particular physical copy.
    # A legacy purchase can leave many unrelated operation rows for the
    # same exact edition and Artifact. The previous un-ordered LIMIT 32
    # could hide the only genuine canonical-intake receipt and permanently
    # block promotion of an already published staging Placement. Never use
    # a legacy receipt as authority: only Core's deterministic namespace can
    # ever match _stage_id(source, offer).
    #
    # Read up to 513 canonical candidates and refuse unbounded/ambiguous
    # snapshots, rather than silently ignoring a genuine proof. This is
    # a read-only SELECT under the already-held acquisition lock.
    journals = db.execute(
        "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
        "WHERE identity_id=? AND provider_id=? AND credential_id=? "
        "AND account_id=? AND item_id=? "
        "AND acquisition_id LIKE 'canonical-intake:%' "
        "AND ((status='complete' AND artifact_id=?) OR "
        "(status='canonical_receipt_staged' AND artifact_id IS NULL)) "
        "ORDER BY acquisition_id LIMIT 513",
        (scope.identity_id, scope.provider_id, scope.credential_id,
         scope.account_id, source.edition_id, row[2]),
    ).fetchall()
    if len(journals) > 512:
        raise ValueError("canonical_stock_receipt_missing_or_ambiguous")
    admissible = []
    for record_id, serialized in journals:
        if type(serialized) is not str or len(serialized) > 65536:
            continue
        try:
            proof = json.loads(
                serialized, object_pairs_hook=_unique_checkpoint_fields,
            )
        except (TypeError, ValueError, RecursionError):
            continue
        if (type(proof) is not dict
                or proof.get("schema") != "canonical.completed-delivery.v1"
                or proof.get("source") != _scope(source)
                or proof.get("path") != row[0]
                or proof.get("sha256") != row[4]
                or type(proof.get("offer_id")) is not str
                or not proof["offer_id"]
                or type(proof.get("receipt")) is not str
                or not proof["receipt"].startswith("core-delivery:")
                or any(ch in proof["receipt"] for ch in "\x00\r\n")
                or type(proof.get("adept_receipt")) is not str
                or any(ch in proof["adept_receipt"] for ch in "\x00\r\n")
                or record_id != _stage_id(source, proof["offer_id"])
                or sum(link[0] == proof["receipt"] for link in references) != 1):
            continue
        admissible.append(proof)
    if len(admissible) != 1:
        raise ValueError("canonical_stock_receipt_missing_or_ambiguous")
    return row, admissible[0]


def _updated_metadata(raw: str, *, old: Path, final: Path, root: Path,
                      catalogue: bool) -> str:
    try:
        metadata = json.loads(raw, object_pairs_hook=_unique_checkpoint_fields)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("canonical_stock_metadata_invalid") from exc
    if type(metadata) is not dict:
        raise ValueError("canonical_stock_metadata_invalid")
    if catalogue:
        prior = metadata.get("path")
        if prior not in (None, str(old), str(final)):
            raise ValueError("canonical_stock_catalog_path_conflict")
        metadata["path"] = str(final)
    else:
        prior = metadata.get("relative_path")
        old_relative = str(old.relative_to(root.resolve()))
        new_relative = str(final.relative_to(root.resolve()))
        if prior not in (None, old_relative, new_relative):
            raise ValueError("canonical_stock_placement_path_conflict")
        metadata["relative_path"] = new_relative
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def repair_exact_published_staging(
    storage, *, source: SourceKey, placement_id: str, library_root: Path,
    shared_lock: InterProcessLock, context_current: Callable[[], bool],
    consent_current: Callable[[], bool],
    expected_source_policy_revision: str | None = None,
    transaction_guard: Callable[[sqlite3.Connection], bool] | None = None,
) -> bool:
    """Move one proven committed staging Placement; False when already final.

    Physical promotion is a verified copy; the old bytes survive until a
    coherent SQLite path update. A failed transaction leaves an intact old
    Placement and recoverable bytes. Re-run with the same source and offer.
    """
    if (type(source) is not SourceKey or type(placement_id) is not str
            or not placement_id or not isinstance(library_root, Path)
            or type(shared_lock) is not InterProcessLock
            or shared_lock._handle is None
            or not callable(context_current) or not callable(consent_current)
            or (transaction_guard is not None and not callable(transaction_guard))):
        raise ValueError("canonical_stock_admission_required")
    # Cheap read-only gate for the overwhelmingly common already-final
    # placement. Never hash hundreds of verified Kobo/other books a second
    # time merely to discover that none is in canonical staging.
    with storage._lock, storage._connect() as db:
        row = db.execute(
            "SELECT p.path,a.sha256 FROM placements AS p "
            "JOIN artifacts AS a ON a.artifact_id=p.artifact_id "
            "WHERE p.placement_id=?", (placement_id,),
        ).fetchone()
    if (row is None or type(row[0]) is not str
            or type(row[1]) is not str):
        raise ValueError("canonical_stock_placement_missing")
    if ".canonical-delivery" not in Path(row[0]).parts:
        return False
    root, profile = _profile(storage, library_root, source)
    if _stage_parts(Path(row[0]), profile) is None:
        return False
    # Recheck the exclusive acquisition lock ONLY for the staging mutation.
    # Normal present-attested placements are read-only and cannot be
    # degraded by an unrelated repair-specific admission configuration.
    if shared_lock.path.resolve() != acquisition_lock_file().resolve():
        raise ValueError("canonical_stock_lock_mismatch")
    # Only verified historical staging may reach the expensive proof/FS gate.
    old = _path_verified(
        storage, root=root, source=source, path=Path(row[0]),
        final_sha256=row[1],
    )
    with storage._lock, storage._connect() as db:
        original, proof = _record(db, source, placement_id)
    if original[0] != str(old):
        raise ValueError("canonical_stock_path_changed")
    before = os.stat(old, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size != original[5]:
        raise ValueError("canonical_stock_bytes_changed")
    from .convergence_execution import CompletedDelivery
    completed = CompletedDelivery(
        source, proof["offer_id"], old, proof["sha256"],
        proof["receipt"], proof["adept_receipt"],
    )
    if context_current() is not True or consent_current() is not True:
        raise ValueError("canonical_stock_context_or_consent_changed")
    final = promote_completed_delivery(
        storage, source=source, completed=completed, library_root=root,
    )
    verified = _path_verified(
        storage, root=root, source=source,
        path=final, final_sha256=proof["sha256"],
    )
    if final == old or verified == old:
        raise ValueError("canonical_stock_promotion_missing")
    after = os.stat(final, follow_symlinks=False)
    if not stat.S_ISREG(after.st_mode):
        raise ValueError("canonical_stock_final_changed")
    if consent_current() is not True:
        raise ValueError("canonical_stock_consent_changed")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # The physical profile directory was resolved before copying bytes.
        # An imported or concurrently rebound registry can map this same
        # directory to another identity. In that case, a staging repair must
        # never rewrite the Placement (even when all SHA/receipt facts match).
        owner_directory = db.execute(
            "SELECT directory_name FROM identities WHERE identity_id=?",
            (source.scope.identity_id,),
        ).fetchone()
        foreign_owner = db.execute(
            "SELECT 1 FROM identities WHERE directory_name=? "
            "AND identity_id<>? LIMIT 1",
            (profile.name, source.scope.identity_id),
        ).fetchone()
        if owner_directory != (profile.name,) or foreign_owner is not None:
            raise ValueError("canonical_stock_profile_ambiguous")
        current, confirmed = _record(db, source, placement_id)
        if current != original or confirmed != proof:
            raise ValueError("canonical_stock_evidence_changed")
        if context_current() is not True:
            raise ValueError("canonical_stock_context_changed")
        if expected_source_policy_revision is not None:
            from .source_sync_consent import policy_in_transaction
            scope = source.scope
            policy = policy_in_transaction(db, ProviderRightsScope(
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id,
            ))
            if (policy.mode != "on"
                    or policy.revision != expected_source_policy_revision):
                raise ValueError("canonical_stock_consent_changed")
        if transaction_guard is not None and transaction_guard(db) is not True:
            raise ValueError("canonical_stock_transaction_guard_changed")
        if (not _same_file(old, before)
                or not _same_file(final, after)
                or db.execute(
                    "SELECT 1 FROM placements WHERE path=? AND placement_id<>?",
                    (str(final), placement_id),
                ).fetchone()):
            raise ValueError("canonical_stock_bytes_or_destination_changed")
        now = int(time.time())
        updated = db.execute(
            "UPDATE placements SET path=?,metadata_json=?,updated_at=? "
            "WHERE placement_id=? AND path=? AND artifact_id=? "
            "AND identity_id=? AND state='present'",
            (str(final), _updated_metadata(
                current[6], old=old, final=final, root=root, catalogue=False,
             ), now, placement_id, str(old), current[2],
             source.scope.identity_id),
        )
        if updated.rowcount != 1:
            raise ValueError("canonical_stock_placement_conflict")
        catalogues = db.execute(
            "SELECT catalog_entry_id,identity_id,artifact_id,metadata_json "
            "FROM catalog_entries WHERE placement_id=?", (placement_id,),
        ).fetchall()
        for entry_id, identity_id, artifact_id, metadata in catalogues:
            if (identity_id not in (None, source.scope.identity_id)
                    or artifact_id not in (None, current[2])):
                raise ValueError("canonical_stock_catalog_owner_conflict")
            db.execute(
                "UPDATE catalog_entries SET metadata_json=?,updated_at=? "
                "WHERE catalog_entry_id=? AND placement_id=?",
                (_updated_metadata(
                    metadata, old=old, final=final, root=root, catalogue=True,
                ), now, entry_id, placement_id),
            )
        if not _same_file(final, after):
            raise ValueError("canonical_stock_final_changed")
        if context_current() is not True:
            raise ValueError("canonical_stock_context_changed")

    # B is now authoritative. The historical checkpoint may still say
    # staged if the old postcommit status update failed. Bring it terminal
    # without treating an operation journal as a provider ownership proof.
    try:
        from .canonical_recovery import mark_completed_delivery_published
        mark_completed_delivery_published(
            storage, source=source, offer_id=proof["offer_id"],
            artifact_id=original[2], shared_lock=shared_lock,
        )
    except Exception:
        _LOG.warning("TRACE convergence code=canonical_stock_checkpoint_deferred")
    # A failure of descriptive index or cleanup cannot revoke OriginLink.
    try:
        result = LocalCatalog(storage, root).index_path(
            final, identity_id=source.scope.identity_id,
        )
        if result.get("status") != "indexed":
            _LOG.warning("TRACE convergence code=canonical_stock_index_deferred")
    except Exception:
        _LOG.warning("TRACE convergence code=canonical_stock_index_deferred")
    try:
        cleanup_published_staging(
            storage, source=source, completed=completed, library_root=root,
        )
    except Exception:
        _LOG.warning("TRACE convergence code=canonical_stock_cleanup_deferred")
    return True


__all__ = ["repair_exact_published_staging"]
