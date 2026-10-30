"""Scoped legacy-Numilog recovery admission; not a new reconciliation engine.

A local receipt/placement is neither download capability nor proof of presence.
The existing authenticated manifest, persistent consent and locked provider
admission remain mandatory. This check ONLY excludes unsafe historical state.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_RECOVERY = ContextVar('cifereca.numilog.recovery', default=False)


@contextmanager
def recovery_request():
    token = _RECOVERY.set(True)
    try:
        yield
    finally:
        _RECOVERY.reset(token)


def recovery_requested():
    return _RECOVERY.get() is True


# Exact startup worker dispatch context, distinct from diagnostics/traces.
# A test or manual caller can open a telemetry trace, but it cannot turn that
# trace into the privilege to bypass local publication guards.
_AUTOMATIC = ContextVar("cifereca.numilog.automatic_dispatch", default=False)


@contextmanager
def automatic_delivery_request():
    token = _AUTOMATIC.set(True)
    try:
        yield
    finally:
        _AUTOMATIC.reset(token)


def automatic_delivery_requested():
    return _AUTOMATIC.get() is True

from ..acquisition_engine import stable_id
from .numilog_manifest_rights import _scoped_record_id


def recovery_id(identity_id, credential_id, account_id, item_id):
    return stable_id(
        "numilog-materialization-recovery", identity_id,
        credential_id, account_id, item_id,
    )


def recovery_gate(storage, *, identity_id, credential_id, account_id, item_id):
    """Return a closed refusal or recoverable for this exact owned purchase.

    Fail closed on foreign owners, incomplete/index-pending
    deliveries, multiple historical receipts or an already attempted recovery.
    Never infer origin from a path, title, ISBN, content hash or directory.
    """
    if (not all(type(v) is str and v for v in
                (identity_id, credential_id, account_id, item_id))
            or _scoped_record_id(item_id, credential_id) is None):
        return "recovery_scope_invalid"
    try:
        evidence = storage.numilog_purchase_preflight_evidence(
            identity_id=identity_id, credential_id=credential_id,
            account_id=account_id, item_id=item_id,
        )
        # #3171, arbitration 2026-10-30: authenticated DOWNLOADABILITY
        # defines the acquisition set. Cached entitlements are optional
        # observations, not an additional local permission.
        # The live session/offer and opt-in are checked by the calling worker
        # and re-admitted by the locked provider executor.
        receipts = evidence.get("acquisitions")
        if type(receipts) is not list or len(receipts) > 1:
            return "recovery_receipts_ambiguous"
        for receipt in receipts:
            if (type(receipt) is not dict
                    or receipt.get("identity_id") != identity_id
                    or receipt.get("account_id") != account_id
                    or receipt.get("item_id") != item_id):
                return "recovery_foreign_receipt"
            if receipt.get("acquisition_id") == recovery_id(
                    identity_id, credential_id, account_id, item_id):
                return "recovery_already_attempted"
            if receipt.get("status") != "complete":
                return "recovery_receipt_pending"
        # A placement belongs to an identity; it has no account column.
        # The exact item ID is credential scoped and MUST be validated above.
        with storage._lock, storage._connect() as db:
            owners = db.execute(
                "SELECT identity_id FROM placements "
                "WHERE provider_id='numilog' AND source_item_id=? LIMIT 3",
                (item_id,),
            ).fetchall()
        if len(owners) > 2 or any(owner[0] != identity_id for owner in owners):
            return "recovery_foreign_placement"
        return "recoverable"
    except Exception:
        return "recovery_evidence_unavailable"
