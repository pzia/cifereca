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
    _path_verified, _scope, _stage_id, _unique_checkpoint_fields,
)
from .source_convergence import SourceKey

_LOG = logging.getLogger("cifereca")


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
    owner = db.execute(
        "SELECT identity_id,enabled FROM provider_connections "
        "WHERE provider_id=? AND credential_id=?",
        (scope.provider_id, scope.credential_id),
    ).fetchone()
    accounts = db.execute(
        "SELECT account_id FROM provider_accounts WHERE identity_id=? "
        "AND provider_id=? AND credential_id=?",
        (scope.identity_id, scope.provider_id, scope.credential_id),
    ).fetchall()
    if (owner != (scope.identity_id, 1)
            or sum(x[0] == scope.account_id for x in accounts) != 1):
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
        "AND s.reference_kind=? AND s.evidence_kind='provider_response'",
        (row[2], scope.identity_id, scope.identity_id, scope.provider_id,
         scope.credential_id, scope.account_id, source.branch,
         source.edition_id, source.right_id, source.kind),
    ).fetchall()
    if not references:
        raise ValueError("canonical_stock_exact_origin_missing")

    # Historical canonical receipts are the independent, per-source proof of
    # delivery; origin by SHA alone cannot bind a particular physical copy.
    journals = db.execute(
        "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
        "WHERE identity_id=? AND provider_id=? AND credential_id=? "
        "AND account_id=? AND item_id=? "
        "AND status='complete' AND artifact_id=? LIMIT 32",
        (scope.identity_id, scope.provider_id, scope.credential_id,
         scope.account_id, source.edition_id, row[2]),
    ).fetchall()
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
            or shared_lock.path.resolve() != acquisition_lock_file().resolve()
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

    # B is now authoritative. A failure of descriptive index or cleanup must
    # not revoke the provider OriginLink or convert success into another GET.
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
