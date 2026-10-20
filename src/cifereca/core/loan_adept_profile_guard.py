"""Borrower/profile preflight for one already-staged loan ACSM (#1120).

This is a mechanical extraction from LibraryService's locked automatic loan
path. The caller retains source admission, existing locks, provider staging,
destination, ADEPT execution, acquisition ledger, retry and all decisions.
Do not look up a different identity, retry ACSM or select a fallback profile.
"""
from __future__ import annotations

from typing import Any

from .adept_borrower import public_borrower_refusal_code
from .loan_adept_profile_receipt import attention_receipt


def check(
    self: Any, loan: Any, *, identity: Any, ledger_base: dict[str, Any],
    previous_meta: dict[str, Any], retained_acsm: Any, trace_ref: str,
    AdeptProfileResolutionError: type, adept_acsm_plan: Any, _TRACE_LOG: Any,
) -> tuple[str, str] | dict[str, Any]:
    """Exact pre-ADEPT grant/profile sequence; dict is the existing refusal."""
    # Check the pinned borrower grant again immediately before ADEPT:
    # provider staging can take time, and a grant may be revoked.
    adept_borrower_identity_id = identity.identity_id
    borrower_code = None
    if str(loan.source_provider).lower() == "paris":
        adept_borrower_identity_id, borrower_code = self._paris_adept_identity_for_loan(
            loan, connection_identity_id=identity.identity_id,
        )
    # Only None means proven: a falsey malformed code must not select ADEPT.
    if borrower_code is not None:
        code = public_borrower_refusal_code(borrower_code)
        self.storage.acquisition_put(
            attention_receipt(
                ledger_base=ledger_base, previous_meta=previous_meta,
                loan_id=loan.id, retained_acsm=retained_acsm, code=code,
            )
        )
        _TRACE_LOG.warning(
            "TRACE download loan_auto blocked reason=ambiguous_borrower_account loan_ref=%s",
            trace_ref,
        )
        return {
            "status": "attention_required", "loan_id": loan.id,
            "code": code, "retry": "explicit_only",
        }
    try:
        profile_name, profile_source = self.resolve_adept_profile_for_identity(adept_borrower_identity_id)
    except AdeptProfileResolutionError as exc:
        # Profile-resolution exceptions may be provided by runtime extensions.
        # Their message and even their code are untrusted: neither belongs in
        # the durable acquisition ledger or the public loan receipt.
        # Resolve a provider/runtime-owned attribute only once. A property
        # may raise, return a non-string object, or change between reads.
        # None of those cases may interrupt the fail-closed ADEPT refusal.
        try:
            untrusted_code = exc.code
        except Exception:
            untrusted_code = None
        code = (untrusted_code if type(untrusted_code) is str
                and untrusted_code in {
                    "adept_profile_required", "adept_profile_ambiguous",
                } else "adept_profile_required")
        message = "Profil ADEPT indisponible"
        self.storage.acquisition_put(
            attention_receipt(
                ledger_base=ledger_base, previous_meta=previous_meta,
                loan_id=loan.id, retained_acsm=retained_acsm, code=code,
                error_message=message,
            )
        )
        _TRACE_LOG.info(
            "TRACE download acsm cleanup loan_ref=%s action=keep reason=profile_unresolved",
            trace_ref,
        )
        return {"status": "attention_required", "loan_id": loan.id, "code": code, "error": message}
    _TRACE_LOG.info(
        "TRACE download adept profile-resolved loan_ref=%s",
        trace_ref,
    )
    # 0.13.3+ distinguishes a reader-backed profile from one that
    # can actually execute ACSM/libgourou operations. Only block
    # when Adept explicitly publishes a negative readiness signal;
    # older contracts that omit it remain compatible.
    try:
        profile_rows = self.kobo_profiles()
    except Exception:
        profile_rows = []
    profile_row = self._adept_profile_row(profile_name, profile_rows)
    if profile_row:
        if adept_acsm_plan.profile_is_explicitly_acsm_unready(profile_row):
            code = "adept_profile_acsm_not_ready"
            message = "Le profil Kobo-Adept lié à cette identité n'est pas prêt pour les acquisitions ACSM"
            self.storage.acquisition_put(
                attention_receipt(
                    ledger_base=ledger_base, previous_meta=previous_meta,
                    loan_id=loan.id, retained_acsm=retained_acsm, code=code,
                    error_message=message, attempt_profile=profile_name,
                    include_attempt_profile=True,
                )
            )
            _TRACE_LOG.info(
                "TRACE download acsm cleanup loan_ref=%s action=keep reason=profile_not_ready",
                trace_ref,
            )
            return {"status": "attention_required", "loan_id": loan.id, "code": code, "error": message}
    return adept_borrower_identity_id, profile_name
