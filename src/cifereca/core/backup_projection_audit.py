"""Core P1 #3196: bounded, exact-scope audit of the Kobo 0.7 projection.

This is evidence for deprecating backup_items, NOT a repair/migration engine.
The canonical entitlement, acquisition and placement tables are authoritative.
Neither matching titles nor matching hashes prove a right; mismatches must
never automatically create, revoke or reactivate rights or delete files.
"""
from __future__ import annotations

from typing import Any


_MAX_ROWS = 500


def audit_backup_projection(
    storage: Any, *, identity_id: str, max_rows: int = _MAX_ROWS,
) -> dict[str, int | str]:
    """Compare one owner's Kobo compatibility rows to canonical SQLite facts.

    A single SQLite savepoint fixes a read snapshot. Return only closed counts
    and a completeness status; no credentials, paths, editions or account IDs.
    An over-budget scan returns *no partial mismatch totals* as an audit fact.
    No network, filesystem probe, provider call or persistent mutation.
    """
    result: dict[str, int | str] = {
        "status": "unavailable", "scanned": 0,
        "missing_entitlement": 0, "missing_acquisition": 0,
        "scope_mismatch": 0, "status_divergence": 0,
        "artifact_divergence": 0, "placement_missing": 0,
        "placement_scope_mismatch": 0,
        # Reverse coverage: canonical acquisitions absent from the legacy
        # mirror are NOT missing rights or a reason to recreate a v0.7 row.
        "canonical_scanned": 0, "canonical_without_projection": 0,
        "canonical_projection_owner_conflict": 0,
        "canonical_projection_scope_unverified": 0,
    }
    if (
        type(identity_id) is not str or not 0 < len(identity_id) <= 1024
        or "\x00" in identity_id
        or type(max_rows) is not int or not 1 <= max_rows <= _MAX_ROWS
    ):
        result["status"] = "invalid_scope"
        return result
    try:
        with storage._lock, storage._connect() as db:
            db.execute("SAVEPOINT backup_projection_audit")
            try:
                columns = (
                    "provider_id,credential_id,account_id,item_id,"
                    "entitlement_type,loan_id,identity_id,status,"
                    "sha256,local_path"
                )
                # Legacy v0.7 rows may not yet have an identity owner.
                # They cannot be attributed to this identity from a book
                # title, credential or SHA. A scoped scan is inconclusive
                # while ANY unscoped Kobo compatibility row remains.
                if db.execute(
                    "SELECT 1 FROM backup_items WHERE provider_id='kobo' "
                    "AND (identity_id IS NULL OR identity_id='') LIMIT 1"
                ).fetchone():
                    result["status"] = "scope_unverified"
                    return result
                rows = db.execute(
                    f"SELECT {columns} FROM backup_items "
                    "WHERE provider_id='kobo' AND identity_id=? "
                    "ORDER BY credential_id,item_id LIMIT ?",
                    (identity_id, max_rows + 1),
                ).fetchall()
                # Read the authoritative acquisitions as well. A legacy-only
                # scan cannot see receipts added directly to the canonical
                # ledger during the forward migration. Both budgets must be
                # complete BEFORE any counts become audit evidence.
                canonical = db.execute(
                    "SELECT credential_id,account_id,item_id FROM acquisition_ledger "
                    "WHERE provider_id='kobo' AND identity_id=? "
                    "ORDER BY credential_id,item_id,account_id LIMIT ?",
                    (identity_id, max_rows + 1),
                ).fetchall()
                if len(rows) > max_rows or len(canonical) > max_rows:
                    result["status"] = "budget_exceeded"
                    return result
                result["canonical_scanned"] = len(canonical)
                for credential, account, item in canonical:
                    if (type(credential) is not str or not credential
                            or type(item) is not str or not item):
                        result["canonical_projection_scope_unverified"] += 1
                        continue
                    legacy = db.execute(
                        "SELECT identity_id,account_id FROM backup_items "
                        "WHERE provider_id='kobo' AND credential_id=? AND item_id=?",
                        (credential, item),
                    ).fetchone()
                    if legacy is None:
                        result["canonical_without_projection"] += 1
                    elif (type(account) is not str or not account
                          or type(legacy[0]) is not str or not legacy[0]
                          or type(legacy[1]) is not str or not legacy[1]):
                        # A missing owner must never be filled from the
                        # currently selected user or the same SHA/title.
                        result["canonical_projection_scope_unverified"] += 1
                    elif legacy != (identity_id, account):
                        result["canonical_projection_owner_conflict"] += 1
                for row in rows:
                    (provider, credential, account, item, right_type,
                     loan_id, owner, status, sha, path) = row
                    fact = {
                        "provider_id": provider, "credential_id": credential,
                        "account_id": account, "item_id": item,
                        "entitlement_type": right_type, "loan_id": loan_id,
                    }
                    expected = (owner, provider, credential, account, item)
                    entitlement_id = storage._entitlement_id_for(fact)
                    acquisition_id = storage._acquisition_id_for(fact)
                    ent = db.execute(
                        "SELECT identity_id,provider_id,credential_id,account_id,item_id "
                        "FROM entitlements WHERE entitlement_id=?",
                        (entitlement_id,),
                    ).fetchone()
                    acq = db.execute(
                        "SELECT identity_id,provider_id,credential_id,account_id,item_id,"
                        "status,artifact_id FROM acquisition_ledger WHERE acquisition_id=?",
                        (acquisition_id,),
                    ).fetchone()
                    if ent is None:
                        result["missing_entitlement"] += 1
                    elif tuple(ent) != expected:
                        result["scope_mismatch"] += 1
                    if acq is None:
                        result["missing_acquisition"] += 1
                    else:
                        if tuple(acq[:5]) != expected:
                            result["scope_mismatch"] += 1
                        # A ledger can progress independently of a stale
                        # backup projection. Report drift; never overwrite it.
                        if acq[5] != storage._generic_status(status):
                            result["status_divergence"] += 1
                        if type(sha) is str and sha and acq[6] != "sha256:" + sha.lower():
                            result["artifact_divergence"] += 1
                    if type(path) is str and path:
                        p = db.execute(
                            "SELECT identity_id,provider_id,source_item_id "
                            "FROM placements WHERE path=? LIMIT 1",
                            (path,),
                        ).fetchone()
                        if p is None:
                            result["placement_missing"] += 1
                        elif tuple(p) != (owner, provider, item):
                            result["placement_scope_mismatch"] += 1
                result["scanned"] = len(rows)
                result["status"] = "complete"
            finally:
                db.execute("RELEASE SAVEPOINT backup_projection_audit")
    except Exception:
        # SQL/schema availability is an audit failure, never a proof of zero
        # mismatches. Keep only the closed status in this failure case.
        return {key: ("unavailable" if key == "status" else 0)
                for key in result}
    return result


__all__ = ["audit_backup_projection"]
