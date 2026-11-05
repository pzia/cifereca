"""#3909: complete deferred post-commit staging maintenance without supplier GET.

A final Placement/OriginLink is authoritative even if Core crashed before
marking its delivery journal 'complete' or removing the private staged copy.
Only the original exact-source, exact-offer Core receipt authorizes cleanup.
This routine is not a recovery/adoption path for unknown provider files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from .canonical_publication import _stable
from .canonical_promotion import _profile, _stage_parts, cleanup_published_staging
from .canonical_recovery import (
    _exact_borrower_bound_in_db, _path_verified, _scope, _stage_id,
    _unique_checkpoint_fields, mark_completed_delivery_published,
)
from .source_convergence import SourceKey

_MAX_RECEIPTS = 512
_MAX_EXACT_ORIGIN_ROWS = 512


def reconcile_committed_staging(
    storage, *, source: SourceKey, placement_id: str, library_root: Path,
    shared_lock: InterProcessLock, current: Callable[[], bool],
    transaction_guard=None,
) -> bool:
    """Finalize one proved post-B staging receipt; False means no safe work.

    A successful B is never downgraded if this maintenance is inconclusive.
    Return True only after the staged copy was actually removed or a staged
    journal was repaired; neither action grants ownership nor issues HTTP.
    """
    if (type(source) is not SourceKey or not isinstance(library_root, Path)
            or type(placement_id) is not str or not placement_id
            or type(shared_lock) is not InterProcessLock
            or shared_lock._handle is None
            or shared_lock.path.resolve() != acquisition_lock_file().resolve()
            or not callable(current)
            or (transaction_guard is not None and not callable(transaction_guard))):
        raise ValueError("canonical_postcommit_admission_required")
    if current() is not True:
        return False
    root, profile = _profile(storage, library_root, source)
    scope = source.scope
    source_id = _stable("source", *_scope(source))
    with storage._lock, storage._connect() as db:
        # A durable OriginLink and receipt cannot authorize maintenance for a
        # borrower now attached to another or disabled provider connection.
        if not _exact_borrower_bound_in_db(db, scope):
            return False
        placed = db.execute(
            "SELECT p.path,p.identity_id,p.artifact_id,p.state,a.sha256 "
            "FROM placements AS p JOIN artifacts AS a "
            "ON a.artifact_id=p.artifact_id WHERE p.placement_id=?",
            (placement_id,),
        ).fetchone()
        if (placed is None or placed[1] != scope.identity_id
                or placed[3] != "present"
                or type(placed[0]) is not str
                or type(placed[4]) is not str
                or placed[2] != "sha256:" + placed[4]):
            return False
        final = Path(placed[0])
        if not final.is_absolute() or final.parent != profile / "Acquisitions":
            return False
        # One operation per exact offer; unrelated legacy receipts cannot
        # claim the same Artifact or hide an existing Core receipt.
        journals = db.execute(
            "SELECT acquisition_id,status,artifact_id,metadata_json "
            "FROM acquisition_ledger "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=? AND item_id=? "
            "AND acquisition_id LIKE 'canonical-intake:%' "
            "AND status IN ('canonical_receipt_staged','complete') "
            "ORDER BY acquisition_id LIMIT ?",
            (scope.identity_id, scope.provider_id, scope.credential_id,
             scope.account_id, source.edition_id, _MAX_RECEIPTS + 1),
        ).fetchall()
        if len(journals) > _MAX_RECEIPTS:
            return False
        links = db.execute(
            "SELECT o.evidence_ref FROM origin_links AS o "
            "JOIN source_references AS s "
            "ON s.source_reference_id=o.source_reference_id "
            "WHERE o.origin_kind='provider' AND o.identity_id=? "
            "AND o.artifact_id=? AND o.source_reference_id=? "
            "AND s.identity_id=? AND s.provider_id=? "
            "AND s.credential_id=? AND s.account_id=? "
            "AND s.branch_id=? AND s.edition_id=? "
            "AND s.right_id=? AND s.reference_kind=? "
            "AND s.evidence_kind='provider_response' "
            "AND s.evidence_ref<>'' "
            "ORDER BY o.origin_link_id LIMIT ?",
            (scope.identity_id, placed[2], source_id, *_scope(source),
             _MAX_EXACT_ORIGIN_ROWS + 1),
        ).fetchall()
        # One exact Core receipt among an unbounded set of other claims
        # does not authorize an arbitrary cleanup of historical staging.
        if not links or len(links) > _MAX_EXACT_ORIGIN_ROWS:
            return False

    candidates = []
    for operation_id, status, artifact_id, serialized in journals:
        if type(serialized) is not str or len(serialized) > 65536:
            continue
        try:
            proof = json.loads(serialized, object_pairs_hook=_unique_checkpoint_fields)
            if (type(proof) is not dict
                    or set(proof) != {"schema", "source", "offer_id", "path",
                                      "sha256", "receipt", "adept_receipt"}
                    or proof["schema"] != "canonical.completed-delivery.v1"
                    or proof["source"] != _scope(source)
                    or type(proof["offer_id"]) is not str
                    or not 0 < len(proof["offer_id"]) <= 1024
                    or operation_id != _stage_id(source, proof["offer_id"])
                    or type(proof["path"]) is not str
                    or type(proof["sha256"]) is not str
                    or proof["sha256"] != placed[4]
                    or type(proof["receipt"]) is not str
                    or not proof["receipt"].startswith("core-delivery:")
                    or len(proof["receipt"]) != len("core-delivery:") + 64
                    or any(c not in "0123456789abcdef"
                           for c in proof["receipt"][len("core-delivery:"):])
                    or type(proof["adept_receipt"]) is not str
                    or len(proof["adept_receipt"]) > 1024
                    or any(c in v for v in (
                        proof["receipt"], proof["adept_receipt"],
                        proof["offer_id"]) for c in "\x00\r\n")
                    or (status == "complete" and artifact_id != placed[2])
                    or (status == "canonical_receipt_staged"
                        and artifact_id is not None)
                    or sum(link[0] == proof["receipt"] for link in links) != 1):
                continue
            staged = Path(proof["path"])
            if not staged.is_absolute():
                continue
            parts = _stage_parts(staged, profile)
            # One Core attempt is a flat, private directory. Never follow
            # nested or traversal paths supplied by a modified checkpoint.
            if (parts is None or len(parts) != 3 or ".." in parts
                    or parts[2] in (".", "..")):
                continue
            coords = (
                scope.identity_id, scope.provider_id, scope.credential_id,
                scope.account_id, source.branch, source.edition_id,
                source.right_id, source.kind, proof["offer_id"],
                proof["receipt"], proof["sha256"],
            )
            target_name = hashlib.sha256(json.dumps(
                coords, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            if final.name not in (target_name + ".pdf", target_name + ".epub"):
                continue
            candidates.append((operation_id, status, serialized, proof, staged))
        except (TypeError, ValueError, IndexError, KeyError, RecursionError):
            continue

    if len(candidates) != 1:
        return False
    operation_id, status, serialized, proof, staged = candidates[0]
    # Reject a symlink or a path replaced between journal and I/O.
    attempt = profile / ".canonical-delivery" / staged.relative_to(profile).parts[1]
    if (profile.is_symlink() or attempt.is_symlink()
            or staged.is_symlink()):
        return False
    # A previously cleaned staging path is legitimate only if no entry in
    # the original attempt was replaced by an unsafe alias.
    missing = not staged.exists()
    if missing:
        if attempt.exists() or attempt.is_symlink():
            return False
        # An already completed receipt with an absent private attempt is
        # the common steady state; avoid another expensive final-file hash.
        if status == "complete":
            return False
    else:
        try:
            if _path_verified(
                storage, root=root, source=source, path=staged,
                final_sha256=proof["sha256"],
            ) != staged:
                return False
        except (ValueError, OSError, RuntimeError):
            return False
    try:
        _path_verified(
            storage, root=root, source=source, path=final,
            final_sha256=proof["sha256"],
        )
    except (ValueError, OSError, RuntimeError):
        return False
    if current() is not True:
        return False
    with storage._lock, storage._connect() as db:
        # Core acquisition lock excludes other Core writers; still reject
        # any independent SQL rebinding between the proof and maintenance.
        same = db.execute(
            "SELECT status,artifact_id,metadata_json FROM acquisition_ledger "
            "WHERE acquisition_id=?", (operation_id,),
        ).fetchone()
        placement = db.execute(
            "SELECT path,artifact_id,state FROM placements "
            "WHERE placement_id=?", (placement_id,),
        ).fetchone()
        linked = db.execute(
            "SELECT 1 FROM origin_links AS o "
            "JOIN source_references AS s "
            "ON s.source_reference_id=o.source_reference_id "
            "WHERE o.origin_kind='provider' "
            "AND o.identity_id=? AND o.artifact_id=? "
            "AND o.source_reference_id=? AND o.evidence_ref=? "
            "AND s.identity_id=? AND s.provider_id=? "
            "AND s.credential_id=? AND s.account_id=? "
            "AND s.branch_id=? AND s.edition_id=? "
            "AND s.right_id=? AND s.reference_kind=? "
            "AND s.evidence_kind='provider_response' "
            "AND s.evidence_ref<>'' LIMIT 1",
            (scope.identity_id, placed[2], source_id, proof["receipt"],
             *_scope(source)),
        ).fetchone()
        if (same != (status, artifact_id, serialized)
                or placement != (str(final), placed[2], "present")
                or linked is None
                # A profile/account may be rebound during the physical hash:
                # check the same exact borrower again before any journal or
                # staged-byte maintenance.
                or not _exact_borrower_bound_in_db(db, scope)
                or (transaction_guard is not None
                    and transaction_guard(db) is not True)):
            return False

    changed = False
    if status == "canonical_receipt_staged":
        mark_completed_delivery_published(
            storage, source=source, offer_id=proof["offer_id"],
            artifact_id=placed[2], shared_lock=shared_lock,
        )
        changed = True
    if not missing:
        # No attempt is deleted unless both physical copies and B were
        # independently checked; never erase a different staged candidate.
        if current() is not True:
            return changed
        from .convergence_execution import CompletedDelivery
        completed = CompletedDelivery(
            source, proof["offer_id"], staged, proof["sha256"],
            proof["receipt"], proof["adept_receipt"],
        )
        if cleanup_published_staging(
            storage, source=source, completed=completed,
            library_root=root,
        ):
            changed = True
    return changed


__all__ = ["reconcile_committed_staging"]
