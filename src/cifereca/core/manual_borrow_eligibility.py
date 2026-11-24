"""Optional, inert provider observations for new manual loan admission."""
from time import time

from ..plugin_api.lending_capabilities import (
    BorrowerOperationEvidence as AccountEvidence,
    assess_borrower_operation_eligibility,
)
from ..plugin_api.lending_borrower_eligibility import (
    BorrowerEligibilitySnapshot, BorrowerOperationEvidence as EditionEvidence,
    assess_borrower_eligibility,
)
from .loan_action_scope import _inert_session_state

_MAX_AGE = 300
_MAX_ROWS = 512

_BLOCKERS = {
    "subscription_not_borrowable": "borrower_subscription_blocked",
    "quota_reached": "borrower_quota_exhausted",
    "borrow_permission_denied": "borrower_permission_denied",
    "subscription_renewal_pending": "borrower_subscription_blocked",
    "subscription_inactive": "borrower_subscription_blocked",
    "borrower_quota_exhausted": "borrower_quota_exhausted",
    "borrower_permission_denied": "borrower_permission_denied",
    "item_offer_unavailable": "item_offer_unavailable",
}
BLOCKER_REASONS = frozenset(_BLOCKERS.values())


def observed_borrow_eligibility(session, account, item, capability, *, now=None):
    """Return (unknown|observed|blocked, closed reason or None).

    Only the session's optional typed publication is read, never a getter,
    catalogue metadata, quota preference, diagnostic or provider method.
    Ambiguous duplicates are unknown; each independent exact denial may veto.
    """
    state = _inert_session_state(session)
    if state is None:
        return "unknown", None
    snapshot = state.get("borrower_eligibility_snapshot")
    if type(snapshot) is not BorrowerEligibilitySnapshot:
        return "unknown", None
    epoch = state.get("_epoch")
    if (type(epoch) not in (int, str)
            or type(snapshot.session_epoch) is not type(epoch)
            or snapshot.session_epoch != epoch
            or type(snapshot.observed_at) is not int
            or type(snapshot.account_evidence) is not tuple
            or type(snapshot.edition_evidence) is not tuple
            or len(snapshot.account_evidence) > _MAX_ROWS
            or len(snapshot.edition_evidence) > _MAX_ROWS):
        return "unknown", None
    if now is None:
        now = int(time())
    if type(now) is not int or not 0 <= now - snapshot.observed_at <= _MAX_AGE:
        return "unknown", None
    owner = (account.identity_id, account.provider_id, account.credential_id,
             account.unique_id, item.docbase, "loan.borrow")
    def account_matches(row):
        return (type(row) is AccountEvidence
                and all(type(v) is str for v in (
                    row.identity_id, row.provider_id, row.credential_id,
                    row.account_id, row.branch, row.operation))
                and (row.identity_id, row.provider_id, row.credential_id,
                     row.account_id, row.branch, row.operation) == owner)

    def edition_matches(row):
        return (type(row) is EditionEvidence
                and all(type(v) is str for v in (
                    row.identity_id, row.provider_id, row.credential_id,
                    row.borrower_id, row.docbase, row.operation, row.record_id))
                and (row.source_collection is None or type(row.source_collection) is str)
                and (row.identity_id, row.provider_id, row.credential_id,
                     row.borrower_id, row.docbase, row.operation) == owner
                and (row.record_id, row.source_collection) ==
                    (item.record_id, item.source_collection))

    accounts = [row for row in snapshot.account_evidence if account_matches(row)]
    editions = [row for row in snapshot.edition_evidence if edition_matches(row)]
    account_fact = assess_borrower_operation_eligibility(
        capability, account=account, branch=item.docbase,
        evidence=accounts[0] if len(accounts) == 1 else None,
    )
    edition_fact = assess_borrower_eligibility(
        capability=capability, account=account, notice=item,
        evidence=editions[0] if len(editions) == 1 else None,
    )
    # A replacement or generation change while reading is not an attestation.
    if (state.get("borrower_eligibility_snapshot") is not snapshot
            or type(state.get("_epoch")) is not type(epoch)
            or state.get("_epoch") != epoch):
        return "unknown", None
    if account_fact.state == "temporarily_blocked":
        return "blocked", _BLOCKERS[account_fact.reason_code]
    if edition_fact.status == "blocked":
        return "blocked", _BLOCKERS[edition_fact.reason_code]
    if (account_fact.state == "eligible_observed"
            and edition_fact.status == "eligible"):
        return "observed", None
    return "unknown", None
