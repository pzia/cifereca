"""Non-authorizing visibility of an explicitly consented existing-loan ACSM action.

Pure read of trusted Core session memory and STATIC provider class declarations.
No GTK selection fallback, provider method/property, transport or storage call.
Final consent, provider right/offer, exact lease and token checks remain in the
existing stage_explicit_loan_acsm executor.
"""
from __future__ import annotations

from inspect import getattr_static, isfunction

from ..models import Account, Loan
from ..providers.base import ProviderDescriptor
from .diagnostic_explicit_loan_action import valid_stage_code
from .loan_action_scope import _inert_session_accounts, _inert_session_state


def explicit_loan_acsm_action_available(service, loan: Loan) -> bool:
    """Expose a button only when the exact connected session declares the ABI.

    Availability is NOT permission to download and does not perform a GET.
    Invalid, dynamic, foreign and ambiguous session data fail closed.
    """
    if type(loan) is not Loan:
        return False
    fields = (
        loan.identity_id, loan.source_provider, loan.source_credential,
        loan.source_account_id, loan.id, loan.record_id,
    )
    if (any(type(value) is not str or not value or len(value) > 1024
            or any(char in value for char in "\0\n\r")
            for value in fields)
            or loan.medium != "digital" or loan.state != "active"):
        return False
    identity_id, provider_id, credential_id, account_id, _, _ = fields
    try:
        # The only registry lookup is an exact pair. Never enumerate a
        # provider's sessions, resolve an omitted credential or call a plugin.
        registry = service._sessions
        if type(registry) is not dict:
            return False
        key = service._session_key(provider_id, credential_id)
        session = registry.get(key)
        if session is None:
            return False
        state = _inert_session_state(session)
        accounts = _inert_session_accounts(session)
        if state is None or accounts is None:
            return False
        descriptor = state.get("descriptor")
        if (type(descriptor) is not ProviderDescriptor
                or descriptor.id != provider_id
                or state.get("provider_id") != provider_id
                or state.get("credential_id") != credential_id
                or state.get("connected") is not True
                or state.get("_closed") is True):
            return False
        # Visibility is scoped to the already selected UI identity. The
        # executor must still independently validate the durable Core owner.
        current_identity = getattr(service, "current_identity", None)
        if getattr(current_identity, "identity_id", None) != identity_id:
            return False
        matched = [
            account for account in accounts
            if type(account) is Account
            and account.identity_id == identity_id
            and account.provider_id == provider_id
            and account.credential_id == credential_id
            and account.unique_id == account_id
        ]
        if len(matched) != 1:
            return False
        klass = type(session)
        profile = getattr_static(klass, "loan_acsm_stage_profile", None)
        if (type(profile) is not tuple or len(profile) != 2
                or type(profile[0]) is not str or not profile[0]
                or type(profile[1]) is not tuple or len(profile[1]) != 5
                or any(not valid_stage_code(code, provider_id)
                       for code in profile[1])):
            return False
        for method in (
            "loan_stage_activity_reason", "loan_stage_candidate_reason",
            "rights_selected_delivery_offer_snapshot",
            "rights_selected_delivery_candidate",
        ):
            if not isfunction(getattr_static(klass, method, None)):
                return False
        return True
    except Exception:
        # Read-only UI projection must not expose plugin exceptions or secrets.
        return False
