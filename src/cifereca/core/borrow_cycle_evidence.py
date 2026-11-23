"""#4082: read-only qualification of a completed loan for manual reborrow."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from ..models import Account, SearchResult, Loan
from ..plugin_api.loan_inventory import LoanInventorySnapshot, LoanInventoryScope


def completed_manual_borrow_cycle(
    *, previous_claim: Any, snapshot: Any, account: Account,
    item: SearchResult, branch: str,
    now: datetime | None = None, max_age_seconds: int = 120,
) -> bool:
    """A prior confirmed loan is absent from fresh COMPLETE exact inventory.

    This is evidence for a new *manual* cycle, not permission or a POST.
    The caller must independently pin the session epoch and atomically
    reserve a new generation under the original borrow claim.
    """
    if (type(previous_claim) is not dict
            or type(snapshot) is not LoanInventorySnapshot
            or type(account) is not Account or type(item) is not SearchResult
            or type(branch) is not str or not branch
            or type(max_age_seconds) is not int or not 0 < max_age_seconds <= 600):
        return False
    if (previous_claim.get("schema") != 1
            or previous_claim.get("operation") != "loan.borrow"
            or previous_claim.get("status") != "confirmed"
            or previous_claim.get("request_accepted") is not True
            or previous_claim.get("verification") != "positive_active_loan"
            or type(previous_claim.get("operation_id")) is not str
            or not previous_claim["operation_id"]
            or type(previous_claim.get("verified_loan_id")) is not str
            or not previous_claim["verified_loan_id"]):
        return False
    if (type(item.record_id) is not str or not item.record_id
            or item.source_provider != account.provider_id
            or item.source_credential != account.credential_id
            or item.medium != "digital"
            or account.is_primary is not True):
        return False
    try:
        # Frozen dataclasses can be mutated via object.__setattr__.
        snapshot.__post_init__()
        if (snapshot.scope != LoanInventoryScope.for_account(account)
                or snapshot.status != "success"
                or snapshot.reason_code is not None
                or len(snapshot.branches) != 1
                or snapshot.branches[0].upstream != branch
                or snapshot.branches[0].medium != "digital"
                or snapshot.branches[0].completeness != "complete"
                or snapshot.branches[0].pagination_complete is not True
                or type(snapshot.observed_at) is not str):
            return False
        seen_at = datetime.fromisoformat(snapshot.observed_at.replace("Z", "+00:00"))
        current = datetime.now(timezone.utc) if now is None else now
        if (type(current) is not datetime or current.tzinfo is None
                or seen_at.tzinfo is None
                or not 0 <= (current-seen_at).total_seconds() <= max_age_seconds):
            return False
        seen_ids = set()
        for loan in snapshot.loans:
            if (type(loan) is not Loan or loan.medium != "digital"
                    or loan.state != "active"
                    or type(loan.record_id) is not str or not loan.record_id
                    or loan.id in seen_ids
                    or loan.id == previous_claim["verified_loan_id"]
                    or loan.record_id == item.record_id):
                return False
            seen_ids.add(loan.id)
        return True
    except Exception:
        return False


__all__ = ["completed_manual_borrow_cycle"]
