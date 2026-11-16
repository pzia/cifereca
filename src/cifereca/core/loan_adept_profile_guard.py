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
from .loan_source_epoch import SourceEpochLease


def refuse_staged_source(
    self: Any, *, ledger_base: dict[str, Any], previous_meta: dict[str, Any],
    retained_acsm: Any, trace_ref: str, expected_loan_id: str,
    raw_code: Any, _TRACE_LOG: Any,
) -> dict[str, Any]:
    """Retain staged ACSM with the exact loan receipt, never a new grant."""
    code = public_borrower_refusal_code(raw_code)
    self.storage.acquisition_put(
        attention_receipt(
            ledger_base=ledger_base, previous_meta=previous_meta,
            loan_id=expected_loan_id, retained_acsm=retained_acsm, code=code,
        )
    )
    _TRACE_LOG.warning(
        "TRACE download loan_auto blocked reason=ambiguous_borrower_account loan_ref=%s",
        trace_ref,
    )
    return {
        "status": "attention_required", "loan_id": expected_loan_id,
        "code": code, "retry": "explicit_only",
    }


def check(
    self: Any, loan: Any, *, identity: Any, ledger_base: dict[str, Any],
    previous_meta: dict[str, Any], retained_acsm: Any, trace_ref: str,
    expected_loan_id: str, expected_record_id: str | None,
    source_lease: SourceEpochLease | None,
    AdeptProfileResolutionError: type, adept_acsm_plan: Any, _TRACE_LOG: Any,
) -> tuple[str, str] | dict[str, Any]:
    """Exact pre-ADEPT grant/profile sequence; dict is the existing refusal."""
    # Check the pinned borrower grant again immediately before ADEPT:
    # provider staging can take time, and a grant may be revoked.
    adept_borrower_identity_id = identity.identity_id
    borrower_code = None
    # The loan may have crossed an external staging boundary since source
    # admission. Do not coerce plugin-owned coordinates, and never interpret
    # malformed provenance as a non-Paris source exempt from the grant guard.
    def source_intact() -> bool:
        # Read live coordinates on each call; a borrower resolver is another
        # external hook capable of mutating the staged Loan.
        return (
            type(expected_loan_id) is str and bool(expected_loan_id)
            and type(expected_record_id) in (str, type(None))
            and type(loan.id) is str and loan.id == expected_loan_id
            and type(loan.record_id) in (str, type(None))
            and loan.record_id == expected_record_id
            and type(loan.source_provider) is str and bool(loan.source_provider)
            and type(loan.source_credential) is str and bool(loan.source_credential)
            and type(loan.source_account_id) in (str, type(None))
            and loan.source_provider == ledger_base.get("provider_id")
            and loan.source_credential == ledger_base.get("credential_id")
            and loan.source_account_id == ledger_base.get("account_id")
            and identity.identity_id == ledger_base.get("identity_id")
            and type(source_lease) is SourceEpochLease
            and source_lease.identity_id == identity.identity_id
            and source_lease.current(self.storage, loan)
        )

    if not source_intact():
        # The durable acquiring receipt is the pinned transaction scope.
        borrower_code = "adept_borrower_ambiguous"
    else:
        # A pinned SQLite epoch is only a source-continuity veto; ADEPT
        # borrower authorization remains source-scoped v16 for all lenders.
        # The legacy "paris" resolver name is not a supplier dispatch.
        try:
            adept_borrower_identity_id, borrower_code = self._paris_adept_identity_for_loan(
                loan, connection_identity_id=identity.identity_id,
            )
        except Exception:
            borrower_code = "adept_borrower_ambiguous"
    # The borrower/grant callback may mutate the staged loan after initial
    # validation. Check the original transaction again before profile lookup.
    if borrower_code is None and not source_intact():
        borrower_code = "adept_borrower_ambiguous"
    # Keep a single durable attention writer for every borrower-scope veto.
    # Profile resolution and inventory are external callbacks too: they must
    # not return a profile for a different staged loan after the first grant.
    def borrower_refusal(raw_code: Any) -> dict[str, Any]:
        return refuse_staged_source(
            self, ledger_base=ledger_base, previous_meta=previous_meta,
            retained_acsm=retained_acsm, trace_ref=trace_ref,
            expected_loan_id=expected_loan_id, raw_code=raw_code,
            _TRACE_LOG=_TRACE_LOG,
        )

    # Only literal None means approved. A profile resolver must never receive
    # an absent/foreign borrower identity even when the guard code was None.
    if borrower_code is not None:
        return borrower_refusal(borrower_code)
    if (type(adept_borrower_identity_id) is not str
            or not adept_borrower_identity_id
            or not source_intact()):
        return borrower_refusal("adept_borrower_ambiguous")
    try:
        profile_name, profile_source = self.resolve_adept_profile_for_identity(adept_borrower_identity_id)
        if not source_intact():
            return borrower_refusal("adept_borrower_ambiguous")
        if type(profile_name) is not str or not profile_name:
            raise AdeptProfileResolutionError(
                "Profil ADEPT indisponible", code="adept_profile_required",
            )
    except AdeptProfileResolutionError as exc:
        if not source_intact():
            return borrower_refusal("adept_borrower_ambiguous")
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
                loan_id=expected_loan_id, retained_acsm=retained_acsm, code=code,
                error_message=message,
            )
        )
        _TRACE_LOG.info(
            "TRACE download acsm cleanup loan_ref=%s action=keep reason=profile_unresolved",
            trace_ref,
        )
        return {"status": "attention_required", "loan_id": expected_loan_id, "code": code, "error": message}
    _TRACE_LOG.info(
        "TRACE download adept profile-resolved loan_ref=%s",
        trace_ref,
    )
    # 0.13.3+ distinguishes a reader-backed profile from one that
    # can actually execute ACSM/libgourou operations. Only block
    # when Adept explicitly publishes a negative readiness signal;
    # older contracts that omit it remain compatible.
    try:
        profile_rows = self.adept_profiles_for_identity(
            adept_borrower_identity_id, profile_name=profile_name,
        )
    except Exception:
        profile_rows = []
    if not source_intact():
        return borrower_refusal("adept_borrower_ambiguous")
    profile_row = self._adept_profile_row(profile_name, profile_rows)
    if not source_intact():
        return borrower_refusal("adept_borrower_ambiguous")
    if profile_row:
        explicitly_unready = adept_acsm_plan.profile_is_explicitly_acsm_unready(
            profile_row,
        )
        if not source_intact():
            return borrower_refusal("adept_borrower_ambiguous")
        if explicitly_unready:
            code = "adept_profile_acsm_not_ready"
            message = "Le profil ADEPT lié à cette identité n'est pas prêt pour les acquisitions ACSM"
            self.storage.acquisition_put(
                attention_receipt(
                    ledger_base=ledger_base, previous_meta=previous_meta,
                    loan_id=expected_loan_id, retained_acsm=retained_acsm, code=code,
                    error_message=message, attempt_profile=profile_name,
                    include_attempt_profile=True,
                )
            )
            _TRACE_LOG.info(
                "TRACE download acsm cleanup loan_ref=%s action=keep reason=profile_not_ready",
                trace_ref,
            )
            return {"status": "attention_required", "loan_id": expected_loan_id, "code": code, "error": message}
    # A final fast recheck excludes mutations by the readiness hook. Never
    # hand an ADEPT profile to the fulfillment stage for a shifted loan.
    if not source_intact():
        return borrower_refusal("adept_borrower_ambiguous")
    return adept_borrower_identity_id, profile_name
