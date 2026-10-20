"""P0 #977: conservative auto-fulfillment retry guard.

A provider download may return an ACSM which is bound to one Adobe identity.
Once ADEPT needs user intervention, the Core must not rerun fulfillment on
every opening merely because the EPUB/PDF file is missing.  A generic CLI
exit code (including 10) is *not* proof of any particular Adobe license error.
"""
from __future__ import annotations

from typing import Any


def blocked_automatic_adept_retry(
    previous: dict[str, Any] | None, *,
    provider_loan_id: str,
    entitlement_id: str | None,
    force_refresh: bool,
) -> bool:
    """True only for an outstanding ADEPT attention on this transaction.

    The acquisition lookup is already scoped by provider, credential, borrower
    account, item and Cifereca identity. We also check the exact transaction
    where known. Pre-fix rows without provenance are held conservatively: they
    are never retried implicitly, but explicit force_refresh can resume them.
    """
    # Only the native True value is an explicit user override. Truthy
    # strings or objects cannot bypass the one-use ACSM retry guard.
    if force_refresh is True or previous is None:
        return False
    # A damaged persisted record must not raise during automatic opening or
    # license a new fetch of a potentially consumed one-use ACSM. Avoid
    # truthiness on foreign objects before checking the exact storage shape.
    if type(previous) is not dict:
        return True
    if not previous:
        return False
    if previous.get("status") != "attention_required":
        return False
    if previous.get("executor") != "kobo-adept" or previous.get("strategy") != "loan_auto":
        return False
    metadata = previous.get("metadata")
    if metadata is None:
        metadata = {}  # Old rows lack this field: retry remains explicit-only.
    if type(metadata) is not dict:
        # A corrupt saved attention is not evidence of a *different* loan.
        # Never consume a potentially one-use ACSM because provenance cannot
        # be decoded; explicit force_refresh remains available to the user.
        return True
    original_loan_id = metadata.get("provider_loan_id")
    if original_loan_id is not None and (
        type(original_loan_id) is not str or not original_loan_id
    ):
        return True
    if original_loan_id and original_loan_id != provider_loan_id:
        return False
    prior_entitlement = previous.get("entitlement_id")
    if prior_entitlement is not None and (
        type(prior_entitlement) is not str or not prior_entitlement
    ):
        return True
    if prior_entitlement and entitlement_id and prior_entitlement != entitlement_id:
        return False
    return True


def adept_attention_code(raw_code: str, message: str, *, executor: str) -> str:
    """Promote only a *named* Adobe server error, never a generic exit code."""
    if executor == "kobo-adept" and "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER" in message.upper():
        return "adept_license_fulfilled_by_other_user"
    return raw_code


__all__ = ["blocked_automatic_adept_retry", "adept_attention_code"]
