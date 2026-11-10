"""Exact provider-neutral selected loan offer, not a grant or delivery receipt.

The caller has already checked its persisted opt-in and source lease.
A provider retains all private activity/offer/GET interpretation; this Core
bridge only checks the typed, exact owner and selected delivery evidence.
"""
from __future__ import annotations

from inspect import getattr_static, isfunction
from types import SimpleNamespace

from .selected_offer_scope import selected_offer_scope_matches


def select_exact_loan_offer(session, account, loan, *, branch: str, current):
    """Return (status, selection) where selection is (snapshot, right, candidate ABI).

    'unavailable' is a partial/missing ABI; 'scope_unproven' is an unmatched
    selected observation or revoked lease. Neither result may trigger GET.
    """
    # A provider-defined property or __getattribute__ is executable code,
    # not proof of an installed selected-offer API. Reject such descriptors
    # *before* dynamic access; this preflight must never issue private I/O.
    klass = type(session)
    if (klass is not SimpleNamespace
            and getattr_static(klass, "__getattribute__", None)
            is not object.__getattribute__):
        return "unavailable", None
    names = (
        "rights_selected_delivery_offer_snapshot",
        "rights_selected_delivery_candidate",
    )
    if any(not isfunction(getattr_static(session, name, None))
           for name in names):
        return "unavailable", None
    offer_snapshot = getattr(session, names[0], None)
    selected_candidate = getattr(session, names[1], None)
    if not callable(offer_snapshot) or not callable(selected_candidate):
        return "unavailable", None
    observed = offer_snapshot(
        account, edition_id=loan.record_id, right_id=loan.id,
        kind="loan", branch=branch, explicit_user_action=True,
    )
    right = observed.exact(loan.record_id, loan.id, "loan")
    if (not selected_offer_scope_matches(
            observed, identity_id=account.identity_id,
            provider_id=loan.source_provider,
            credential_id=account.credential_id,
            account_id=account.unique_id, branch=branch,
        )
            or right is None or right.provider_attested is not True
            or right.delivery_attested is not True
            or type(right.delivery_offer_id) is not str
            or not right.delivery_offer_id or not current()):
        return "scope_unproven", None
    return "selected", (observed, right, selected_candidate)


__all__ = ["select_exact_loan_offer"]
