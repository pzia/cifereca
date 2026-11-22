"""#976: compare recorded transaction coordinates with a supplied ADEPT inventory.

This is a private, read-only diagnostic. The controller supplies an already
read, profile-scoped inventory. This function cannot attest its transport or
freshness, and never calls a runtime or repairs a binding.
"""
from __future__ import annotations

import json
import sqlite3

from .canonical_recovery import _unique_checkpoint_fields
from .adept_historical_snapshot import historical_read_snapshot
from .source_convergence import SourceKey

_MAX_LEDGER_ROWS = 256
_MAX_JSON_BYTES = 65536
_MAX_LOANS = 4096


def _text(value, *, empty=False):
    return (type(value) is str and len(value) <= 1024
            and (bool(value) or empty)
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def _recorded_binding(storage, source):
    scope = source.scope
    with historical_read_snapshot(storage) as db:
        rows = db.execute(
            "SELECT status,executor,CASE WHEN length(CAST(metadata_json AS BLOB))<=? "
            "THEN metadata_json ELSE NULL END FROM acquisition_ledger "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=? AND item_id=? AND strategy='loan_auto' LIMIT ?",
            (_MAX_JSON_BYTES, scope.identity_id, scope.provider_id, scope.credential_id,
             scope.account_id, source.edition_id, _MAX_LEDGER_ROWS + 1),
        ).fetchall()
        if len(rows) > _MAX_LEDGER_ROWS:
            return "budget_exceeded", None
        matches = []
        for status, executor, raw in rows:
            if type(raw) is not str:
                return "ledger_unverified", None
            metadata = json.loads(raw, object_pairs_hook=_unique_checkpoint_fields)
            if type(metadata) is not dict or not _text(metadata.get("provider_loan_id")):
                return "ledger_unverified", None
            if metadata["provider_loan_id"] == source.right_id:
                matches.append((status, executor, metadata))
        if not matches:
            return "not_recorded", None
        if len(matches) != 1:
            return "ambiguous_transaction", None
        status, executor, metadata = matches[0]
        if status != "complete" or executor != "kobo-adept":
            return "historical_attempt_or_incomplete", None
        binding = metadata.get("adept_binding")
        if (type(binding) is not dict
                or not _text(binding.get("adept_profile_id"))
                or not _text(binding.get("adept_loan_id"))):
            return "binding_incomplete", None
        return "recorded", (binding["adept_profile_id"], binding["adept_loan_id"])


def review_legacy_adept_inventory(
    storage, *, source: SourceKey, inventory_profile_id: str,
    inventory_complete: bool, inventory_loans,
):
    """Compare a single exact historical loan with a supplied inventory.

    inventory_profile_id must be the independently established stable profile
    ID, not a name/alias. A match only corroborates the recorded loan/profile;
    it cannot prove actual fulfillment, canonical bytes, provider availability
    or authorization. All identifiers remain private.
    """
    def result(recorded, comparison="not_checked", state="not_checked", token=None):
        return {
            "recorded_binding": recorded, "inventory_comparison": comparison,
            "adept_license_state_reported": state, "token_present_reported": token,
            "provider_loan_state": "not_checked", "canonical_placement_state": "not_checked",
            "activation_attested": False, "inventory_transport_attested": False,
            "may_rebind_or_return": False, "may_reacquire": False,
        }

    if type(source) is not SourceKey or source.kind != "loan":
        return result("scope_unverified")
    try:
        status, pair = _recorded_binding(storage, source)
        if pair is None:
            return result(status)
        profile_id, loan_id = pair
        if not _text(inventory_profile_id) or profile_id != inventory_profile_id:
            return result(status, "profile_mismatch")
        if inventory_complete is not True:
            return result(status, "inventory_incomplete")
        if type(inventory_loans) not in (tuple, list) or len(inventory_loans) > _MAX_LOANS:
            return result(status, "inventory_unverified")
        exact = []
        for loan in inventory_loans:
            if type(loan) is not dict or not _text(loan.get("id")):
                return result(status, "inventory_unverified")
            if loan["id"] == loan_id:
                exact.append(loan)
        if not exact:
            return result(status, "not_found_in_supplied_inventory")
        if len(exact) != 1:
            return result(status, "ambiguous_loan")
        loan = exact[0]
        returned, expired, token = (loan.get(key) for key in ("returned", "expired", "token_present"))
        validity = loan.get("validity")
        if (not all(type(flag) is bool for flag in (returned, expired, token))
                or (validity is not None and not _text(validity, empty=True))):
            return result(status, "inventory_unverified")
        if returned and token:
            state = "contradictory_report"
        elif returned:
            state = "returned_reported"
        elif expired:
            state = "expired_reported"
        elif validity:
            state = "active_reported"
        else:
            state = "state_unverified"
        # No positive result if the historical transaction changed while
        # correlating the supplied observations.
        if _recorded_binding(storage, source) != (status, pair):
            return result("ledger_changed")
        return result(status, "exact_profile_and_loan_reported", state, token)
    except (sqlite3.Error, ValueError, TypeError, RuntimeError, RecursionError):
        return result("ledger_unverified")
