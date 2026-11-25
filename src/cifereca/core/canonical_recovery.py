"""#3171: durable *completed-delivery* handoff between GET and SQLite B commit.

The legacy acquisition_ledger is an OPERATION JOURNAL, never ownership or a
replacement OriginLink. A journal row is created only AFTER final bytes, SHA
and an independent completed-delivery receipt are verified. On retry, the
exact currently admitted source/offer may reuse those bytes without a second
GET, but only through the same Core executor/session/consent/SQLite guards.
No blob is inferred from a matching filename, SHA or old backup_items row.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time

from ..catalog import LocalCatalog
from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from .canonical_publication import _physical_target, _same_file, _stable, _canonical_write_transaction
from .source_convergence import SourceKey
from .canonical_adept_attempt import release_completed_attempt_in_transaction
from .canonical_runtime_attempt import release_runtime_attempt_in_transaction

_SCHEMA = "canonical.completed-delivery.v1"
# The generic opening recovery rewrites *every* status='acquiring' ledger
# row to retryable_error, indiscriminately. A completed-delivery checkpoint
# is NOT an in-flight download and must survive that legacy startup sweep.
_STAGED = "canonical_receipt_staged"


def _require_idle_connection(storage):
    # Storage's worker-local connection is not a nested transaction API.
    # Check before helpers that own the non-reentrant storage lock.
    if storage._connect().in_transaction:
        raise ValueError("canonical_intake_nested_transaction")


def _checkpoint(db, record_id):
    return db.execute(
        "SELECT provider_id,credential_id,account_id,item_id,identity_id,"
        "status,artifact_id,metadata_json FROM acquisition_ledger "
        "WHERE acquisition_id=?", (record_id,),
    ).fetchone()


def _unique_checkpoint_fields(pairs):
    """Never turn ambiguous persisted JSON into a completed-delivery receipt."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("canonical_intake_duplicate_checkpoint_field")
        result[key] = value
    return result


def _exact_borrower_bound_in_db(db, scope) -> bool:
    """Check the sole enabled connection and its exact borrower in one snapshot.

    The journal's caller already holds Storage's lock and BEGIN. A nested
    Storage call would deadlock, while materializing a whole credential roster
    makes every completed GET and retry scale with unrelated accounts.
    """
    connections = db.execute(
        "SELECT connection_id,identity_id,enabled FROM provider_connections "
        "WHERE provider_id=? AND credential_id=? LIMIT 2",
        (scope.provider_id, scope.credential_id),
    ).fetchall()
    if (len(connections) != 1
            or connections[0][1:] != (scope.identity_id, 1)):
        return False
    accounts = db.execute(
        "SELECT 1 FROM provider_accounts WHERE connection_id=? "
        "AND identity_id=? AND provider_id=? AND credential_id=? "
        "AND account_id=? LIMIT 2",
        (connections[0][0], scope.identity_id, scope.provider_id,
         scope.credential_id, scope.account_id),
    ).fetchall()
    return len(accounts) == 1


def _stage_id(source: SourceKey, offer_id: str) -> str:
    scope = source.scope
    return _stable(
        "canonical-intake", scope.identity_id, scope.provider_id,
        scope.credential_id, scope.account_id, source.branch,
        source.edition_id, source.right_id, source.kind, offer_id,
    )


def _scope(source: SourceKey) -> list[str]:
    scope = source.scope
    return [
        scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id, source.branch, source.edition_id,
        source.right_id, source.kind,
    ]


def _lock_ok(lock: InterProcessLock) -> bool:
    return (
        type(lock) is InterProcessLock
        and lock._handle is not None
        and lock.path.resolve() == acquisition_lock_file().resolve()
    )


def _path_verified(storage, *, root: Path, source: SourceKey,
                   path: Path, final_sha256: str) -> Path:
    if (not isinstance(root, Path) or not isinstance(path, Path)
            or type(final_sha256) is not str or len(final_sha256) != 64
            or any(c not in "0123456789abcdef" for c in final_sha256)):
        raise ValueError("canonical_intake_invalid_path_or_hash")
    directory, unique = storage.exact_profile_directory_binding(source.scope.identity_id)
    if type(directory) is not str or unique is not True:
        raise ValueError("canonical_intake_profile_unavailable")
    actual = _physical_target(path, root, directory)
    before = os.stat(actual, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
        raise ValueError("canonical_intake_invalid_final_bytes")
    fmt = LocalCatalog._media_format(actual)
    if fmt not in ("epub", "kepub", "pdf"):
        raise ValueError("canonical_intake_invalid_media_type")
    valid, _reason = LocalCatalog._validate(actual, fmt)
    if not valid or LocalCatalog.sha256(actual) != final_sha256 or not _same_file(actual, before):
        raise ValueError("canonical_intake_final_sha_not_verified")
    # Validation/hash may take time. A changed or newly shared physical
    # owner cannot attest the same receipt, even when the bytes match.
    if storage.exact_profile_directory_binding(source.scope.identity_id) != (directory, True):
        raise ValueError("canonical_intake_profile_unavailable")
    return actual


def record_completed_delivery_for_recovery(
    storage, *, source: SourceKey, offer_id: str, completed,
    library_root: Path, shared_lock: InterProcessLock,
) -> str:
    """Only Core calls this after an authorized, completed supplier delivery.

    The caller revalidates session/consent before and after this operation.
    SQLite still revalidates the owner's enabled credential and exact account,
    under BEGIN IMMEDIATE. This is a journal, not a successful acquisition.
    """
    # Lazy import prevents a cycle with the executor.
    from .convergence_execution import CompletedDelivery

    if (type(source) is not SourceKey or not _lock_ok(shared_lock)
            or type(offer_id) is not str or not offer_id
            or len(offer_id) > 1024
            or type(completed) is not CompletedDelivery
            or completed.source != source
            or completed.delivery_offer_id != offer_id
            or type(completed.receipt_ref) is not str or not completed.receipt_ref
            or len(completed.receipt_ref) > 1024
            or type(completed.adept_receipt_ref) is not str
            or len(completed.adept_receipt_ref) > 1024
            or type(completed.runtime_attempt_id) is not str
            or (completed.runtime_attempt_id and completed.adept_attempt_id)
            or type(completed.adept_attempt_id) is not str
            or (completed.adept_attempt_id and (
                source.kind != "loan" or not completed.adept_receipt_ref))
            or any("\x00" in x or "\r" in x or "\n" in x for x in (
                offer_id, completed.receipt_ref, completed.adept_receipt_ref,
            ))):
        raise ValueError("canonical_intake_completed_offer_required")
    _require_idle_connection(storage)
    actual = _path_verified(
        storage, root=library_root, source=source,
        path=completed.path, final_sha256=completed.final_sha256,
    )
    scope = source.scope
    record_id = _stage_id(source, offer_id)
    stage = {
        "schema": _SCHEMA, "source": _scope(source),
        "offer_id": offer_id, "path": str(actual),
        "sha256": completed.final_sha256,
        "receipt": completed.receipt_ref,
        "adept_receipt": completed.adept_receipt_ref,
    }
    data = json.dumps(stage, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    now = int(time.time())
    with _canonical_write_transaction(storage) as db:
        if not _exact_borrower_bound_in_db(db, scope):
            raise ValueError("canonical_intake_account_rebound")
        row = db.execute(
            "SELECT provider_id,credential_id,account_id,item_id,identity_id,"
            "status,artifact_id,metadata_json FROM acquisition_ledger "
            "WHERE acquisition_id=?",
            (record_id,),
        ).fetchone()
        coords = (
            scope.provider_id, scope.credential_id, scope.account_id,
            source.edition_id, scope.identity_id,
        )
        if row is None:
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,provider_id,credential_id,account_id,item_id,"
                "identity_id,status,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (record_id, *coords, _STAGED, data, now, now),
            )
        elif row[:5] != coords:
            raise ValueError("canonical_intake_record_collision")
        elif row[5] in ("complete", _STAGED):
            # The same supplier offer may be legally reissued after the
            # original file was removed. A NEW completed GET and its new
            # receipt replace only this *non-authoritative* retry checkpoint,
            # never the provider/ADEPT historical OriginLinks. Reset a
            # corrupt, non-null Artifact pointer even when the new bytes and
            # receipt JSON are identical: another crash must remain retryable.
            if row[7] != data or row[5] != _STAGED or row[6] is not None:
                db.execute(
                    "UPDATE acquisition_ledger SET status=?,"
                    "metadata_json=?,artifact_id=NULL,updated_at=? "
                    "WHERE acquisition_id=?", (_STAGED, data, now, record_id),
                )
        else:
            raise ValueError("canonical_intake_record_state_conflict")
        # Clear only the very attempt whose independently verified bytes
        # are now recoverable, in the same SQLite transaction. If the
        # checkpoint fails or the process dies earlier, the hold survives.
        release_completed_attempt_in_transaction(
            db, source=source, offer_id=offer_id,
            operation_id=completed.adept_attempt_id,
        )
        release_runtime_attempt_in_transaction(
            db, source=source, offer_id=offer_id,
            operation_id=completed.runtime_attempt_id,
        )
    return record_id


def recover_completed_delivery(
    storage, *, source: SourceKey, offer_id: str,
    library_root: Path, shared_lock: InterProcessLock,
):
    """Read a previously persisted final receipt; never invent a provider GET.

    The executing Core Sync must have CURRENT positive A + offer + consent,
    and still perform its ordinary session/epoch revalidation and canonical B
    publication. Unavailable/contradictory journal state is treated as NO
    RECOVERY, not as local satisfaction or permission to download.
    """
    from .convergence_execution import CompletedDelivery

    if (type(source) is not SourceKey or not _lock_ok(shared_lock)
            or type(offer_id) is not str or not offer_id
            or len(offer_id) > 1024):
        return None
    _require_idle_connection(storage)
    record_id = _stage_id(source, offer_id)
    scope = source.scope
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        row = _checkpoint(db, record_id)
        if row is None or row[:5] != (
            scope.provider_id, scope.credential_id, scope.account_id,
            source.edition_id, scope.identity_id,
        ) or row[5] not in (_STAGED, "complete"):
            return None
        # A staged receipt has not published an Artifact yet. A completed
        # journal must name the EXACT final SHA of its receipt. Otherwise an
        # inconsistent operation record cannot be reinterpreted as a verified
        # delivery, even when the physical file itself still hashes correctly.
        if row[5] == _STAGED and row[6] is not None:
            return None
        if not _exact_borrower_bound_in_db(db, scope):
            return None
        try:
            # A damaged operation journal cannot permanently trap the source
            # in a broken recovery loop. A valid receipt record is bounded by
            # the source coordinates, offer, pathname and two receipt refs.
            if type(row[7]) is not str or len(row[7]) > 65536:
                return None
            raw = json.loads(row[7], object_pairs_hook=_unique_checkpoint_fields)
            if (type(raw) is not dict or raw.get("schema") != _SCHEMA
                    or raw.get("source") != _scope(source)
                    or raw.get("offer_id") != offer_id
                    or type(raw.get("path")) is not str
                    or type(raw.get("sha256")) is not str
                    or type(raw.get("receipt")) is not str
                    or not raw["receipt"] or len(raw["receipt"]) > 1024
                    or any(ch in raw["receipt"] for ch in "\x00\r\n")
                    or type(raw.get("adept_receipt")) is not str
                    or len(raw["adept_receipt"]) > 1024
                    or any(ch in raw["adept_receipt"] for ch in "\x00\r\n")):
                return None
            if (row[5] == "complete" and (
                    type(raw.get("sha256")) is not str
                    or row[6] != "sha256:" + raw["sha256"])):
                return None
            completed = CompletedDelivery(
                source=source, delivery_offer_id=offer_id,
                path=Path(raw["path"]), final_sha256=raw["sha256"],
                receipt_ref=raw["receipt"],
                adept_receipt_ref=raw["adept_receipt"],
            )
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            return None
    try:
        _path_verified(
            storage, root=library_root, source=source,
            path=completed.path, final_sha256=completed.final_sha256,
        )
    except (ValueError, OSError, RuntimeError):
        return None
    # Physical verification ran without the SQL lock. A concurrent writer
    # may have changed this exact receipt or its account. Do not reuse stale
    # evidence OR interpret this racing transaction as absence/GET consent.
    _require_idle_connection(storage)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        if (not _lock_ok(shared_lock) or _checkpoint(db, record_id) != row
                or not _exact_borrower_bound_in_db(db, scope)):
            raise ValueError("canonical_intake_checkpoint_changed")
    return completed


def _published_receipt_links_current(db, source, artifact_id, provider_receipt, adept_receipt):
    """Reuse the exact canonical B proof inside an existing SQL transaction."""
    scope = source.scope
    source_id = _stable(
        "source", scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id, source.branch, source.edition_id,
        source.right_id, source.kind,
    )
    def has_exact_link(kind, receipt):
        origin_id = _stable(
            "origin", kind, scope.identity_id, artifact_id,
            source_id, receipt,
        )
        return db.execute(
            "SELECT 1 FROM source_references s "
            "JOIN origin_links o ON o.source_reference_id=s.source_reference_id "
            "JOIN artifacts a ON a.artifact_id=o.artifact_id "
            "WHERE s.source_reference_id=? AND s.identity_id=? "
            "AND s.provider_id=? AND s.credential_id=? AND s.account_id=? "
            "AND s.branch_id=? AND s.edition_id=? AND s.right_id=? "
            "AND s.reference_kind=? AND s.evidence_kind='provider_response' "
            "AND s.evidence_ref<>'' AND o.origin_link_id=? "
            "AND o.origin_kind=? AND o.identity_id=? "
            "AND o.artifact_id=? AND o.evidence_ref=? LIMIT 1",
            (source_id, scope.identity_id, scope.provider_id,
             scope.credential_id, scope.account_id, source.branch,
             source.edition_id, source.right_id, source.kind,
             origin_id, kind, scope.identity_id, artifact_id, receipt),
        ).fetchone() is not None
    return (has_exact_link("provider", provider_receipt)
            and (not adept_receipt or has_exact_link("adept", adept_receipt)))


def _relocated_completed_checkpoint(db, record_id, row, *, identity_id, artifact_id,
                                    old_path, new_path):
    """Return only the relocated operation journal, never modify origin history.

    Call inside the common relocation transaction after staging and checking
    the same physical file. Unknown, partial or ambiguous records fail closed.
    """
    from ..plugin_api.provider_rights_observation import ProviderRightsScope

    if (row[5] != "complete" or row[6] != artifact_id
            or row[4] != identity_id or type(artifact_id) is not str
            or len(artifact_id) != 71 or not artifact_id.startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in artifact_id[7:])
            or type(row[7]) is not str or len(row[7]) > 65536):
        raise ValueError("relocate_acquisition_reference_requires_relink")
    try:
        data = json.loads(row[7], object_pairs_hook=_unique_checkpoint_fields)
        if (type(data) is not dict or set(data) != {
                "schema", "source", "offer_id", "path", "sha256", "receipt", "adept_receipt"}
                or data["schema"] != _SCHEMA or data["path"] != old_path
                or data["sha256"] != artifact_id[7:]
                or type(data["source"]) is not list or len(data["source"]) != 8):
            raise ValueError("invalid_checkpoint")
        owner, provider, credential, account, branch, edition, right, kind = data["source"]
        scope = ProviderRightsScope(owner, provider, credential, account)
        source = SourceKey(scope, edition, right, kind, branch)
        offer, receipt, adept = data["offer_id"], data["receipt"], data["adept_receipt"]
        if (row[:5] != (provider, credential, account, edition, owner)
                or owner != identity_id
                or any(type(v) is not str or len(v) > 1024
                       or any(c in v for c in "\x00\r\n")
                       for v in (offer, receipt, adept))
                or not offer or not receipt
                or _stage_id(source, offer) != record_id
                or not _exact_borrower_bound_in_db(db, scope)
                or not _published_receipt_links_current(db, source, artifact_id, receipt, adept)):
            raise ValueError("invalid_checkpoint")
        from .duplicate_maintenance_guard import _mentions_exact_path
        if any(_mentions_exact_path(v, old_path) for k, v in data.items() if k != "path"):
            raise ValueError("invalid_checkpoint")
        data["path"] = new_path
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError("relocate_acquisition_reference_requires_relink") from None


def mark_completed_delivery_published(
    storage, *, source: SourceKey, offer_id: str,
    artifact_id: str, shared_lock: InterProcessLock,
) -> None:
    """Best-effort operation journal status; canonical OriginLink is authority."""
    if (type(source) is not SourceKey or not _lock_ok(shared_lock)
            or type(offer_id) is not str
            or type(artifact_id) is not str
            or not artifact_id.startswith("sha256:")):
        raise ValueError("canonical_intake_invalid_published_transition")
    if (len(artifact_id) != 71
            or any(c not in "0123456789abcdef" for c in artifact_id[7:])
            or not offer_id or len(offer_id) > 1024
            or any(c in offer_id for c in "\x00\r\n")):
        raise ValueError("canonical_intake_invalid_published_transition")
    _require_idle_connection(storage)
    now = int(time.time())
    with _canonical_write_transaction(storage) as db:
        row = _checkpoint(db, _stage_id(source, offer_id))
        scope = source.scope
        if row is None or row[5] not in (_STAGED, "complete"):
            raise ValueError("canonical_intake_checkpoint_missing")
        if (row[:5] != (scope.provider_id, scope.credential_id, scope.account_id,
                        source.edition_id, scope.identity_id)
                or not _exact_borrower_bound_in_db(db, scope)
                or type(row[7]) is not str or len(row[7]) > 65536):
            raise ValueError("canonical_intake_checkpoint_conflict")
        data = json.loads(row[7], object_pairs_hook=_unique_checkpoint_fields)
        if (type(data) is not dict or data.get("schema") != _SCHEMA
                or data.get("source") != _scope(source)
                or data.get("offer_id") != offer_id
                or data.get("sha256") != artifact_id[7:]):
            raise ValueError("canonical_intake_checkpoint_conflict")
        if ((row[5] == "complete" and row[6] != artifact_id)
                or (row[5] == _STAGED and row[6] is not None)):
            raise ValueError("canonical_intake_checkpoint_conflict")
        # A matching SHA/receipt in the operation journal is not B. The
        # terminal status follows the publisher's independently committed
        # source, provider OriginLink and present Placement, never precedes it.
        provider_receipt = data.get("receipt")
        adept_receipt = data.get("adept_receipt")
        if (type(provider_receipt) is not str or not provider_receipt
                or len(provider_receipt) > 1024
                or type(adept_receipt) is not str or len(adept_receipt) > 1024
                or any(ch in item for item in (provider_receipt, adept_receipt)
                       for ch in "\x00\r\n")):
            raise ValueError("canonical_intake_checkpoint_conflict")
        if (not _published_receipt_links_current(
                db, source, artifact_id, provider_receipt, adept_receipt)
                or db.execute(
                    "SELECT 1 FROM placements WHERE identity_id=? "
                    "AND artifact_id=? AND state='present' LIMIT 1",
                    (scope.identity_id, artifact_id),
                ).fetchone() is None):
            raise ValueError("canonical_intake_published_origin_unverified")
        # Idempotence requires the same already-committed B, not only a
        # terminal operation flag left behind after provenance drift.
        if row[5] == "complete":
            return
        db.execute(
            "UPDATE acquisition_ledger SET status='complete',artifact_id=?,"
            "updated_at=? WHERE acquisition_id=? AND status=?",
            (artifact_id, now, _stage_id(source, offer_id), _STAGED),
        )


__all__ = [
    "record_completed_delivery_for_recovery",
    "recover_completed_delivery",
    "mark_completed_delivery_published",
]
