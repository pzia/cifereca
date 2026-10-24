"""Core boundary for a deliberately requested BnF loan ACSM, not a new loan.

Never use the automatic acquisition path, infer a borrower from the selected
account, create entitlements, or start Adobe fulfillment. Only a Web loan
positively observed in the exact connected provider session can be fetched.
"""
from __future__ import annotations
from pathlib import Path

from ..models import Loan
from ..plugin_api import ProviderConfigurationError

_MAX_BYTES = 2 * 1024 * 1024


def _refuse(code: str):
    raise ProviderConfigurationError(code)


def stage_bnf_loan_acsm(
    service, loan: Loan, *, confirmed: bool, expected_identity_id: str,
    interprocess_lock, lock_path, busy_error, inspect_artifact,
) -> dict[str, str]:
    """Stage an ACSM candidate only after caller's affirmative consent."""
    if confirmed is not True:
        _refuse("bnf_explicit_confirmation_required")
    if (type(loan) is not Loan or loan.source_provider != "bnf-pret"
            or loan.medium != "digital" or loan.state != "active"
            or type(loan.source_credential) is not str or not loan.source_credential
            or type(loan.source_account_id) is not str or not loan.source_account_id
            or type(loan.identity_id) is not str or not loan.identity_id
            or type(loan.id) is not str or not loan.id
            or type(loan.record_id) is not str or not loan.record_id
            or type(expected_identity_id) is not str
            or expected_identity_id != loan.identity_id):
        _refuse("bnf_loan_scope_unproven")

    def active_identity():
        return (service.current_identity is not None
                and service.current_identity.identity_id == expected_identity_id
                and service.storage.identity_for_connection(
                    loan.source_provider, loan.source_credential) == expected_identity_id)

    if not active_identity():
        _refuse("bnf_loan_scope_unproven")
    try:
        with interprocess_lock(lock_path()):
            if not active_identity():
                _refuse("bnf_loan_scope_unproven")
            try:
                # No primary/legacy fallback: source_account_id was mandatory.
                account = service._account_for_loan(loan)
                session = service._session_for_source(
                    loan.source_provider, loan.source_credential)
                if (account.identity_id != expected_identity_id
                        or account.provider_id != "bnf-pret"
                        or account.credential_id != loan.source_credential
                        or account.unique_id != loan.source_account_id
                        or session.connected is not True
                        or session.provider_id != "bnf-pret"
                        or not any(a is account for a in session.accounts)):
                    _refuse("bnf_loan_scope_unproven")
            except Exception:
                _refuse("bnf_loan_scope_unproven")

            # No provider download capability override. The BnF-specific
            # explicit gate is required; generic download_digital(loan,...)
            # remains refused, including during opening_sync.
            destination = service._acquisition_work_dir(
                expected_identity_id, "bnf-pret", loan.id)
            try:
                artifact, url = session.download_digital(
                    loan, account, destination, explicit_user_action=True)
            except Exception:
                # Do not surface supplier exception text: even a parser may
                # accidentally include private URLs or SAML in that text.
                _refuse("bnf_loan_token_unavailable")
            try:
                if not isinstance(artifact, Path) or type(url) is not str or url:
                    _refuse("bnf_loan_token_invalid")
                if (destination.is_symlink() or artifact.is_symlink()
                        or artifact.parent.resolve() != destination.resolve()
                        or artifact.suffix.casefold() != ".acsm"
                        or not artifact.is_file()
                        or not 0 < artifact.stat().st_size <= _MAX_BYTES
                        or not inspect_artifact(artifact).valid):
                    _refuse("bnf_loan_token_invalid")
                if not active_identity() or session.connected is not True:
                    try:
                        artifact.unlink(missing_ok=True)
                    except OSError:
                        pass
                    _refuse("bnf_loan_scope_unproven")
            except (OSError, ValueError):
                _refuse("bnf_loan_token_invalid")
            # The candidate is deliberately NOT an acquisition, a loan
            # fulfillment receipt, or a new/expired entitlement.
            return {"status": "candidate_acsm", "path": str(artifact)}
    except busy_error:
        _refuse("bnf_acquisition_busy")
