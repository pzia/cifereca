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
    # Check the exact source-scoped borrower grant again immediately before
    # ADEPT for EVERY ACSM provider: staging can take time and grants can
    # change. Direct EPUB/PDF acquisitions do not enter this helper.
    try:
        adept_borrower_identity_id = identity.identity_id
    except Exception:
        adept_borrower_identity_id = None
    borrower_code = None
    # The loan may have crossed an external staging boundary since source
    # admission. Do not coerce plugin-owned coordinates, and never interpret
    # malformed provenance as an exemption from the grant guard.
    def source_intact() -> bool:
        # All coordinates are plain native values from the original staged
        # transaction. An external getter or SQLite hook may mutate Loan;
        # fail closed rather than using an updated provider/account.
        if (type(expected_loan_id) is not str or not expected_loan_id
                or type(expected_record_id) not in (str, type(None))
                or type(ledger_base) is not dict
                or type(source_lease) is not SourceEpochLease):
            return False
        try:
            loan_id = loan.id
            record_id = loan.record_id
            provider = loan.source_provider
            credential = loan.source_credential
            account = loan.source_account_id
            identity_id = identity.identity_id
            if (type(loan_id) is not str or loan_id != expected_loan_id
                    or type(record_id) not in (str, type(None))
                    or record_id != expected_record_id
                    or type(provider) is not str or not provider
                    or provider != ledger_base.get("provider_id")
                    or type(credential) is not str or not credential
                    or credential != ledger_base.get("credential_id")
                    or type(account) not in (str, type(None))
                    or account != ledger_base.get("account_id")
                    or type(identity_id) is not str or not identity_id
                    or identity_id != ledger_base.get("identity_id")
                    or source_lease.identity_id != identity_id):
                return False
            if self.storage.provider_connection_owner_current(
                provider, credential, identity_id,
            ) is not True:
                return False
            return source_lease.current(self.storage, loan)
        except Exception:
            return False

    if not source_intact():
        # The durable acquiring receipt is the pinned transaction scope.
        borrower_code = "adept_borrower_ambiguous"
    else:
        # This helper is invoked ONLY for an already staged ACSM, regardless
        # of its issuing library. ADEPT needs the exact borrowing identity
        # for every source: a non-Paris linked reader must not inherit the
        # connection owner's Adobe profile without a v16 source-scoped grant.
        # The historical facade name is retained for compatibility; the
        # resolver uses v16 grants for all sources and reads v15 only for Paris.
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
