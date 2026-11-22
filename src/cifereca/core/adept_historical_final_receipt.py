"""#7244: local completed-delivery observation for an exact historical hold.

No permission, activation, publication or new provider request. Reuse the
canonical checkpoint reader under the existing acquisition lock. The exact
receipt is private; only its observation state leaves this helper.
"""
from hashlib import sha256
import json
from pathlib import Path

from ..locking import InterProcessLock
from . import canonical_recovery as recovery
from . import canonical_adept_attempt as marker
from .adept_historical_snapshot import historical_read_snapshot
from .library_sqlite_freshness import sqlite_source_stamp

_MAX_CHECKPOINTS = 64


def observe_final_receipt(request, source, reviewed_marker, stamp):
    """Observe final bytes only for the same recorded offer and full source."""
    def live():
        return (request.bound() and sqlite_source_stamp(request.database) == stamp)

    if reviewed_marker.state != "held_phase_unknown":
        return "unverified"
    storage = request.storage
    try:
        if not live():
            return "unverified"
        # Never create/truncate the acquisition lock for a read-only review.
        with InterProcessLock(recovery.acquisition_lock_file(),
                              existing_nofollow=True) as lock:
            with historical_read_snapshot(storage) as db:
                if not marker.matches_reviewed_loan_adept_marker_in_transaction(
                        db, source=source, review=reviewed_marker):
                    return "unverified"
                raw = marker._decode_marker(marker._marker_row(db, source)[0])
                scope = source.scope
                rows = db.execute(
                    "SELECT acquisition_id,CASE WHEN length(CAST(metadata_json AS BLOB)) "
                    "BETWEEN 1 AND 65536 THEN metadata_json ELSE NULL END FROM acquisition_ledger "
                    "WHERE identity_id=? AND provider_id=? AND credential_id=? AND account_id=? "
                    "AND item_id=? AND status IN (?,?) LIMIT ?",
                    (scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id,
                     source.edition_id, recovery._STAGED, "complete", _MAX_CHECKPOINTS + 1),
                ).fetchall()
            if len(rows) > _MAX_CHECKPOINTS or not live() or not lock.strict_current():
                return "unverified"
            offers = []
            for record, body in rows:
                if type(body) is not str:
                    return "unverified"
                data = json.loads(body, object_pairs_hook=recovery._unique_checkpoint_fields)
                if type(data) is not dict:
                    return "unverified"
                if data.get("schema") != recovery._SCHEMA:
                    # Historical ledger records are not completed receipts.
                    continue
                offer = data.get("offer_id")
                if (type(offer) is not str or not 0 < len(offer) <= 1024
                        or data.get("source") != recovery._scope(source)):
                    continue
                if (sha256(offer.encode()).hexdigest() == raw["offer_digest"]
                        and record == recovery._stage_id(source, offer)):
                    offers.append(offer)
            if len(offers) != 1:
                return "unverified" if offers else "not_observed"
            completed = recovery.recover_completed_delivery(
                storage, source=source, offer_id=offers[0],
                library_root=Path(request.root), shared_lock=lock)
            if not lock.strict_current() or not live():
                return "unverified"
            if completed is not None and completed.adept_receipt_ref:
                return "verified_final_receipt_observed"
            return "unverified"
    except Exception:
        return "unverified"
