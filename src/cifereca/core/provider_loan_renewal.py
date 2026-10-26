"""Core P1 #3121: explicit Paris renewal with fresh borrower and durable claim.

A user's selected Loan is not permission and its raw Syracuse data may be
stale/absent. This path reads one exact connected borrower, validates positive
eligibility, and records an at-most-once claim *before* RenewLoans. A provider
HTTP acknowledgement is only submitted, never independently confirmed.
No ADEPT, acquisition, right closure or extra provider mutation is permitted.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import uuid
from typing import Any

from ..models import Loan
from ..plugin_api.loan_inventory import read_loan_inventory
from .loan_action_scope import capture_loan_action_scope


def _public(status: str, code: str, *, accepted: bool = False) -> dict[str, Any]:
    """Closed result only: no title, borrower, id, URL or provider exception."""
    return {
        "status": status, "code": code,
        "provider_request_accepted": accepted is True,
        "provider_verified": False,
        "retry": "verification_only" if status in {
            "submitted", "verification_unavailable",
        } else "explicit_only",
    }


def _source_key(loan: Loan) -> str | None:
    if type(loan) is not Loan:
        return None
    parts = (
        loan.source_provider, loan.source_credential,
        loan.source_account_id, loan.provider, loan.id,
    )
    if (any(type(part) is not str or not part or len(part) > 1024
            or chr(0) in part for part in parts)
            or loan.source_provider != "paris"):
        return None
    payload = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return "loan.renew.v1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _same_positive_loan(candidate: Any, selected: Loan, account: Any) -> bool:
    """An exact positive row, not an exhaustive negative provider inventory."""
    if type(candidate) is not Loan:
        return False
    keys = (
        candidate.id, candidate.record_id, candidate.provider,
        candidate.medium, candidate.source_provider,
        candidate.source_credential, candidate.source_account_id,
    )
    if (any(type(value) is not str or not value or len(value) > 1024
            or chr(0) in value for value in keys)
            or not (candidate.identity_id is None
                    or (type(candidate.identity_id) is str
                        and candidate.identity_id == account.identity_id))
            or candidate.id != selected.id
            or candidate.record_id != selected.record_id
            or candidate.provider != selected.provider
            or candidate.medium != selected.medium
            or candidate.source_provider != selected.source_provider
            or candidate.source_credential != selected.source_credential
            or candidate.source_account_id != selected.source_account_id
            or candidate.renewable is not True):
        return False
    # Paris's RenewLoans POST sends loan.raw, not the public Loan DTO.
    # Without one matching upstream Id and positive CanRenew, NEVER POST.
    raw = candidate.raw
    if (type(raw) is not dict or type(raw.get("CanRenew")) is not bool
            or raw["CanRenew"] is not True):
        return False
    raw_id = raw.get("Id") or raw.get("HoldingId")
    if type(raw_id) is not str or raw_id != candidate.id:
        return False
    if (candidate.state is not None and
            (type(candidate.state) is not str
             or candidate.state.casefold() in {
                "returned", "closed", "expired", "cancelled", "canceled",
             })):
        return False
    return True


def renew(self, loan: Loan) -> dict[str, Any]:
    """Called by the Core facade after strict manual consent and its lock."""
    key = _source_key(loan)
    if (key is None
            or type(loan.identity_id) is not str or not loan.identity_id
            or type(loan.record_id) is not str or not loan.record_id
            or type(loan.medium) is not str
            or loan.medium not in {"digital", "physical"}
            or (loan.state is not None and
                (type(loan.state) is not str
                 or loan.state.casefold() in {
                    "returned", "closed", "expired", "cancelled", "canceled",
                }))):
        return _public("blocked", "loan_scope_unverified")

    # The selected Loan can be mutated by a GUI refresh while this worker
    # reads Paris. Bind consent to its exact scalar identity, not its object.
    selected_scope = (
        loan.identity_id, loan.source_provider, loan.source_credential,
        loan.source_account_id, loan.id, loan.record_id, loan.provider,
        loan.medium,
    )
    previous = self.storage.loan_renewal_state(key)
    if previous is not None:
        if previous["status"] == "submitted":
            return _public("submitted", "renewal_previous_submission",
                           accepted=previous.get("request_accepted") is True)
        return _public(
            "verification_unavailable", "renewal_previous_attempt_unverified",
        )

    try:
        # The strict Paris preflight rejects an unknown borrower; never use the
        # currently selected account or another credential as a fallback.
        account, session, _return_key = self._return_loan_identity(loan)
        if (account.identity_id != loan.identity_id
                or session.connected is not True
                or session.provider_id != "paris"
                or session.credential_id != loan.source_credential
                or getattr(session.capabilities, "renew_loan", None) is not True):
            return _public("blocked", "loan_scope_unverified")
        # Loan.state from older Paris models may be None even when CanRenew is
        # attested upstream. This normalized *scope copy* cannot authorize
        # dispatch by itself; a fresh verified loan is mandatory below.
        scope_loan = replace(
            loan, state=loan.state if type(loan.state) is str else "active",
        )
        lease = capture_loan_action_scope(
            self, scope_loan, account, session,
            expected_identity_id=loan.identity_id,
        )
        if lease is None:
            return _public("blocked", "loan_scope_unverified")
    except Exception:
        return _public("blocked", "loan_scope_unverified")

    try:
        # Never trust the GUI/cache's raw JSON. Paris's own normalizing reader
        # stamps exact borrower provenance on each loan. Partial sibling
        # upstream results do not invalidate a *positive* exact row.
        inventory = read_loan_inventory(session, account)
    except Exception:
        return _public("blocked", "loan_read_unavailable")
    if (not lease.current(self)
            or (
                loan.identity_id, loan.source_provider, loan.source_credential,
                loan.source_account_id, loan.id, loan.record_id, loan.provider,
                loan.medium,
            ) != selected_scope):
        return _public("blocked", "loan_scope_changed")
    if inventory.status not in {'success', 'partial'}:
        return _public("blocked", "loan_read_unavailable")
    fresh = inventory.loans
    positives = [row for row in fresh if _same_positive_loan(row, loan, account)]
    # A borrowed ID can occur on multiple upstream branches. An ambiguous
    # same-ID row also prevents permission even if one branch looked valid.
    same_id = [
        row for row in fresh
        if type(row) is Loan and type(row.id) is str and row.id == loan.id
    ]
    if len(positives) != 1 or len(same_id) != 1:
        return _public("blocked", "loan_permission_unverified")

    # Only the server's positively renewable original row is sent to Paris.
    # A fabricated/state-normalized clone carries the exact raw provider
    # request but only the Core-scoped identity; never mutate the cache DTO.
    candidate = replace(
        positives[0],
        identity_id=account.identity_id,
        state=positives[0].state if type(positives[0].state) is str else "active",
    )
    verified = capture_loan_action_scope(
        self, candidate, account, session,
        expected_identity_id=account.identity_id,
    )
    if verified is None or not lease.current(self):
        return _public("blocked", "loan_scope_changed")

    # Atomic persistent checkpoint: once reserved, neither a timeout,
    # restart nor a second click is permission for another RenewLoans POST.
    operation_id = uuid.uuid4().hex
    try:
        claimed, _receipt = self.storage.loan_renewal_claim_once(key, operation_id)
    except Exception:
        return _public("blocked", "renewal_checkpoint_unavailable")
    if not claimed:
        return _public(
            "verification_unavailable", "renewal_previous_attempt_unverified",
        )
    if (not lease.current(self) or not verified.current(self)
            or (
                loan.identity_id, loan.source_provider, loan.source_credential,
                loan.source_account_id, loan.id, loan.record_id, loan.provider,
                loan.medium,
            ) != selected_scope):
        self.storage.loan_renewal_finish(
            key, operation_id, status="blocked", accepted=False,
        )
        return _public("blocked", "loan_scope_changed")

    accepted = False
    try:
        response = session.renew(candidate, account)
        accepted = (
            type(response) is dict
            and (response.get("Renewed") is True
                 or ("Renewed" not in response
                     and response.get("success") is True))
            and response.get("success") is not False
        )
    except Exception:
        # An exception does not prove that the POST was not accepted remotely.
        pass

    still_bound = (
        lease.current(self) and verified.current(self)
        and (
            loan.identity_id, loan.source_provider, loan.source_credential,
            loan.source_account_id, loan.id, loan.record_id, loan.provider,
            loan.medium,
        ) == selected_scope
    )
    if not still_bound or not accepted:
        status = "verification_unavailable"
        reason = (
            "loan_scope_changed" if not still_bound else
            "renewal_provider_response_unverified"
        )
    else:
        status = "submitted"
        reason = "renewal_provider_submitted"
    try:
        persisted = self.storage.loan_renewal_finish(
            key, operation_id, status=status, accepted=accepted,
        )
    except Exception:
        persisted = None
    if persisted is None:
        return _public(
            "verification_unavailable", "renewal_checkpoint_unavailable",
        )
    # The remote effect may exist even after a timeout. Invalidate old cached
    # loans, but do NOT persist a new due date or report "confirmed".
    try:
        self.storage.cache_delete_namespace("account")
    except Exception:
        pass
    return _public(status, reason, accepted=accepted)


__all__ = ["renew"]
