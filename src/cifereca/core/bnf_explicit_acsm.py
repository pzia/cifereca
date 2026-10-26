"""Core boundary for a deliberately requested BnF loan ACSM, not a new loan.

Never use the automatic acquisition path, infer a borrower from the selected
account, create entitlements, or start Adobe fulfillment. Only a Web loan
positively observed in the exact connected provider session can be fetched.
"""
from __future__ import annotations
from pathlib import Path
from xml.etree import ElementTree

from ..models import Loan
from ..plugin_api import ProviderConfigurationError
from .diagnostic_bnf_loan_action import BnfLoanActionError, provider_reason
from .loan_action_scope import capture_loan_action_scope

_MAX_BYTES = 2 * 1024 * 1024


def _refuse(code: str):
    raise BnfLoanActionError(code) from None


def _valid_loan_token(path: Path, inspect_artifact) -> bool:
    """Independent Core admission: a valid XML file alone is not a loan ACSM.

    The provider already checks its HTTP response. Recheck the persisted
    candidate here so another plugin implementation or damaged staging cannot
    pass a purchase, an entity-bearing XML, or a false file suffix to ADEPT.
    No token body or attributes leave this local function.
    """
    inspection = inspect_artifact(path)
    if inspection.valid is not True or inspection.media_format != "acsm":
        return False
    with path.open("rb") as handle:
        content = handle.read(_MAX_BYTES + 1)
    if not 0 < len(content) <= _MAX_BYTES:
        return False
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        return False
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError, TypeError):
        return False
    return (
        root.tag == "{http://ns.adobe.com/adept}fulfillmentToken"
        and root.attrib.get("fulfillmentType") == "loan"
    )


def _discard_invalid_candidate(path: object, destination: Path) -> None:
    """Delete only an untrusted ACSM inside our exact staging directory.

    The provider return value is not trusted: never unlink an arbitrary path
    or follow a symlink when rejecting a candidate.
    """
    if not isinstance(path, Path):
        return
    try:
        if path.suffix.casefold() != ".acsm":
            return
        if (not destination.is_symlink() and not path.is_symlink()
                and path.parent.resolve() == destination.resolve()
                and path.is_file()):
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def stage_bnf_loan_acsm(
    service, loan: Loan, *, confirmed: bool, expected_identity_id: str,
    interprocess_lock, lock_path, busy_error, inspect_artifact,
    observe_phase=lambda _phase: None,
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

    observe_phase('source_scope')
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
                        or session.credential_id != loan.source_credential
                        or not any(a is account for a in session.accounts)):
                    _refuse("bnf_loan_scope_unproven")
            except Exception:
                _refuse("bnf_loan_scope_unproven")

            # Consent is already explicit, and this BnF-private action is
            # deliberately NOT the publicly advertised loan.download_token
            # capability. Capture the exact Core account/session/epoch before
            # any staging or provider GET. A same-object in-place mutation
            # must not retain its former authority.
            lease = capture_loan_action_scope(
                service, loan, account, session,
                expected_identity_id=expected_identity_id,
            )
            if lease is None:
                _refuse("bnf_loan_scope_unproven")
            observe_phase('local_staging')
            destination = service._acquisition_work_dir(
                expected_identity_id, "bnf-pret", loan.id)
            observe_phase('provider_delivery')
            # A context change after confirmation (or while local staging
            # was being prepared) must deny the GET itself, not just its
            # resulting candidate.
            if not lease.current(service):
                _refuse("bnf_loan_scope_unproven")
            try:
                artifact, url = session.download_digital(
                    loan, account, destination, explicit_user_action=True)
            except Exception as exc:
                # Do not surface supplier exception text: even a parser may
                # accidentally include private URLs or SAML in that text.
                _refuse(provider_reason(exc))
            observe_phase('artifact_validation')
            # Network I/O can switch the Core epoch even if the selected
            # identity later returns to its original value (ABA). Discard
            # only the private candidate in our own staging directory.
            if not lease.current(service):
                _discard_invalid_candidate(artifact, destination)
                _refuse("bnf_loan_scope_unproven")
            try:
                if not isinstance(artifact, Path) or type(url) is not str or url:
                    _refuse("bnf_loan_token_invalid")
                if (destination.is_symlink() or artifact.is_symlink()
                        or artifact.parent.resolve() != destination.resolve()
                        or artifact.suffix.casefold() != ".acsm"
                        or not artifact.is_file()
                        or not 0 < artifact.stat().st_size <= _MAX_BYTES
                        or not _valid_loan_token(artifact, inspect_artifact)):
                    _refuse("bnf_loan_token_invalid")
                if not lease.current(service):
                    _discard_invalid_candidate(artifact, destination)
                    _refuse("bnf_loan_scope_unproven")
            except (OSError, ValueError):
                _discard_invalid_candidate(artifact, destination)
                _refuse("bnf_loan_token_invalid")
            except ProviderConfigurationError:
                _discard_invalid_candidate(artifact, destination)
                raise
            # The borrower/session may have been replaced during HTTP.
            # A valid ACSM from a stale session must never be handed to ADEPT.
            observe_phase('source_revalidation')
            try:
                still_bound = lease.current(service)
            except Exception:
                still_bound = False
            if not still_bound:
                _discard_invalid_candidate(artifact, destination)
                _refuse("bnf_loan_scope_unproven")
            # The candidate is deliberately NOT an acquisition, a loan
            # fulfillment receipt, or a new/expired entitlement.
            return {"status": "candidate_acsm", "path": str(artifact)}
    except busy_error:
        _refuse("bnf_acquisition_busy")
