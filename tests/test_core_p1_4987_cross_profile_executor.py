"""#4987: one cross-profile supplier attempt, no identity switch or copy.

The synthetic provider verifies a Loan but performs no real network I/O.
SQLite checkpoints are checked before provider call and across replays.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.models import Loan
from cifereca.plugin_api import BorrowRequestNotSubmitted
from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p1_4662_live_lender_preview import (
    ParisOffline, _candidate, _preview, harness,
)


class BorrowingOffline(ParisOffline):
    def __init__(self, account, service, outcome="confirmed"):
        super().__init__(account)
        self.service = service
        self.outcome = outcome
        self.actions = 0
        self.callbacks = []

    def borrow_explicit_with_claim(
        self, item, account, *, admission, claimed_receipt,
        confirmed, core_scope_current,
    ):
        self.actions += 1
        assert admission.admitted is True
        assert confirmed is True
        assert claimed_receipt["status"] == "request_sent"
        key = borrow_claim_key(account, item)
        assert self.service.storage.loan_borrow_state(key)["status"] == "request_sent"
        assert self.service.current_identity.identity_id == "default"
        self.callbacks.append(core_scope_current())
        if self.outcome == "unsent":
            raise BorrowRequestNotSubmitted("no upstream mutation")
        if self.outcome == "unknown":
            raise RuntimeError("/private/loan/token")
        if self.outcome == "rebind":
            self.service._source_context_epoch += 1
        loan = Loan(
            id="loan-synthetic", provider=item.docbase, medium="digital",
            record_id=item.record_id, title="Synthetic",
            identity_id=account.identity_id,
            source_provider=account.provider_id,
            source_credential=account.credential_id,
            source_account_id=account.unique_id,
            state="active",
        )
        if self.outcome == "wrong_lender":
            loan.identity_id = "default"
        if self.outcome == "inactive":
            loan.state = "expired"
        if isinstance(self.outcome, str) and self.outcome.startswith("state:"):
            loan.state = self.outcome.removeprefix("state:")
        return loan


def setup_borrower(harness, outcome="confirmed"):
    service, account, _ = harness
    session = BorrowingOffline(account, service, outcome)
    service._sessions[service._session_key(
        account.provider_id, account.credential_id,
    )] = session
    row = _candidate()
    preview = _preview(service, row)
    assert preview.status == "external" and len(preview.options) == 1
    return service, account, session, row, preview


def execute(service, row, preview, *, confirmed=True):
    return service.execute_cross_profile_borrow(
        captured_preview=preview,
        selected_option_id=preview.options[0].option_id,
        expected_edition_id="edition-1",
        observations=(row,), confirmed=confirmed, now=1100,
    )


def test_explicit_lender_loan_confirms_once_without_profile_switch_or_copy(harness):
    service, account, session, row, preview = setup_borrower(harness)
    requester = service.current_identity
    service.materialize_to_identity = lambda *_a, **_k: pytest.fail(
        "an observed lender loan never licenses a transfer"
    )
    result = execute(service, row, preview)
    assert result == {
        "status": "confirmed",
        "reason_code": "lender_loan_confirmed_transfer_unverified",
        "provider_verified": True, "delivery": "unavailable",
        "transfer": "blocked", "retry_borrow": False,
    }
    assert session.actions == 1 and session.callbacks == [True]
    assert service.current_identity is requester
    claim = service.storage.loan_borrow_state(borrow_claim_key(account, row.notice))
    assert claim["status"] == "confirmed"
    assert claim["verified_loan_id"] == "loan-synthetic"
    # The existing read-only progress must consume the *same* lender claim.
    # It reports the verified loan but never a delivered or transferable book.
    progress = service.cross_profile_borrow_progress(
        requesting_identity_id="default", borrowing_account=account,
        exact_notice=row.notice,
    )
    assert (progress.loan, progress.delivery, progress.transfer) == (
        "confirmed", "unavailable", "blocked",
    )
    assert progress.retry_borrow is progress.executable is False
    # A second SQLite connection (standing in for process restart) must
    # observe the *same* lender-owned reservation, not a requester claim.
    from cifereca.storage import Storage
    reopened = Storage(service.storage.path)
    try:
        assert reopened.loan_borrow_claim_once(
            borrow_claim_key(account, row.notice), "new-process-operation",
        )[0] is False
        assert reopened.loan_borrow_state(
            borrow_claim_key(account, row.notice),
        )["status"] == "confirmed"
    finally:
        reopened.close()
    assert execute(service, row, preview)["status"] == "blocked"
    assert session.actions == 1


@pytest.mark.parametrize("outcome,expected", [
    ("unknown", "verification_unavailable"),
    ("wrong_lender", "verification_unavailable"),
    ("rebind", "verification_unavailable"),
    ("inactive", "verification_unavailable"),
])
def test_ambiguous_or_foreign_result_consumes_claim_no_auto_retry(
    harness, outcome, expected,
):
    service, account, session, row, preview = setup_borrower(harness, outcome)
    result = execute(service, row, preview)
    assert result["status"] == expected
    assert result["retry_borrow"] is False
    assert result["transfer"] == "blocked"
    assert session.actions == 1
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, row.notice),
    )["status"] == "verification_unavailable"
    assert execute(service, row, preview)["status"] == "blocked"
    assert session.actions == 1


def test_unsent_refusal_only_releases_claim_with_provider_proof(harness):
    service, account, session, row, preview = setup_borrower(harness, "unsent")
    result = execute(service, row, preview)
    assert result["reason_code"] == "borrow_not_submitted"
    assert result["retry_borrow"] is False
    assert service.storage.metadata_get(
        borrow_claim_key(account, row.notice),
    ) is None
    assert session.actions == 1


@pytest.mark.parametrize("mutation", ["confirmation", "stale_identity", "stale_source", "disabled"])
def test_no_consent_or_stale_lender_does_not_consume_claim(harness, mutation):
    service, account, session, row, preview = setup_borrower(harness)
    if mutation == "stale_identity":
        service.current_identity = replace(service.current_identity,
                                           identity_id="alex")
    elif mutation == "stale_source":
        service._source_context_epoch += 1
    elif mutation == "disabled":
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "card-alex", "label": "Disabled",
            "enabled": False, "metadata": {},
        })
    result = execute(service, row, preview, confirmed=mutation != "confirmation")
    assert result["status"] == "blocked"
    assert session.actions == 0
    assert service.storage.metadata_get(
        borrow_claim_key(account, row.notice),
    ) is None


def test_duplicate_exact_observation_never_falls_back_to_first(harness):
    service, account, session, row, preview = setup_borrower(harness)
    result = service.execute_cross_profile_borrow(
        captured_preview=preview,
        selected_option_id=preview.options[0].option_id,
        expected_edition_id="edition-1", observations=(row, row),
        confirmed=True, now=1100,
    )
    assert result["status"] == "blocked"
    assert session.actions == 0
    assert service.storage.metadata_get(
        borrow_claim_key(account, row.notice),
    ) is None


def test_source_rebind_during_atomic_claim_blocks_provider_dispatch(
    harness, monkeypatch,
):
    service, account, session, row, preview = setup_borrower(harness)
    original = service.storage.loan_borrow_claim_once

    def revoked_at_commit(key, operation):
        result = original(key, operation)
        service._source_context_epoch += 1
        return result

    monkeypatch.setattr(
        service.storage, "loan_borrow_claim_once", revoked_at_commit,
    )
    result = execute(service, row, preview)
    assert result["status"] == "blocked"
    assert result["transfer"] == "blocked"
    assert session.actions == 0
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, row.notice),
    )["status"] == "blocked"


def test_reader_rebound_during_confirmed_checkpoint_suppresses_positive_reply(
    harness, monkeypatch,
):
    service, account, session, row, preview = setup_borrower(harness)
    original = service.storage.loan_borrow_finish

    def rebind_on_finish(*args, **kwargs):
        receipt = original(*args, **kwargs)
        if kwargs.get("status") == "confirmed":
            service.current_identity = replace(service.current_identity)
        return receipt

    monkeypatch.setattr(service.storage, "loan_borrow_finish", rebind_on_finish)
    result = execute(service, row, preview)
    assert result["status"] == "verification_unavailable"
    assert result["provider_verified"] is False
    assert result["retry_borrow"] is False
    assert session.actions == 1
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, row.notice),
    )["status"] == "confirmed"


def test_provider_rebound_during_return_does_not_confirm_loan(harness, monkeypatch):
    # Even when the returned Loan mirrors the account's new provider,
    # a mid-request change of the pinned account must revoke the lease.
    service, account, session, row, preview = setup_borrower(harness)
    original = session.borrow_explicit_with_claim

    def forged_alias(*args, **kwargs):
        loan = original(*args, **kwargs)
        loan.source_account_id = "primary"
        account.provider_id = "bnf-pret"
        loan.source_provider = "bnf-pret"
        return loan

    # A mid-call mutation of account provider must also revoke scope.
    monkeypatch.setattr(session, "borrow_explicit_with_claim", forged_alias)
    result = execute(service, row, preview)
    assert result["status"] == "verification_unavailable"
    assert result["provider_verified"] is False
    assert result["retry_borrow"] is False
    assert service.storage.loan_borrow_state(
        borrow_claim_key(replace(account, provider_id="paris"), row.notice),
    )["status"] == "verification_unavailable"


@pytest.mark.parametrize("revoke", ["lender_plugin", "manifest", "global_plugin"])
def test_plugin_reconfiguration_during_claim_cannot_dispatch_provider(
    harness, monkeypatch, revoke,
):
    service, account, session, row, preview = setup_borrower(harness)
    manager = service._plugin_manager
    original = service.storage.loan_borrow_claim_once
    original_status = manager.status
    switch = {"active": True}
    manager.enabled_for_identity = lambda _plugin, _identity: (
        switch["active"] if revoke == "lender_plugin" else True
    )
    if revoke == "manifest":
        manager.status = lambda plugin_id: (
            original_status(plugin_id) if switch["active"] else
            __import__("types").SimpleNamespace(
                state="ready", enabled=True,
                descriptor=__import__("types").SimpleNamespace(
                    id=plugin_id, type="provider.library", capabilities=(),
                ),
            )
        )
    elif revoke == "global_plugin":
        manager.status = lambda plugin_id: (
            original_status(plugin_id) if switch["active"] else
            __import__("types").SimpleNamespace(
                state="ready", enabled=False,
                descriptor=original_status(plugin_id).descriptor,
            )
        )

    def revoke_at_claim(key, operation):
        result = original(key, operation)
        switch["active"] = False
        return result

    monkeypatch.setattr(service.storage, "loan_borrow_claim_once", revoke_at_claim)
    outcome = execute(service, row, preview)
    assert outcome["status"] == "blocked"
    assert outcome["retry_borrow"] is False
    assert session.actions == 0
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, row.notice),
    )["status"] == "blocked"


@pytest.mark.parametrize("gate", ["missing", "disabled"])
def test_per_identity_plugin_admission_must_be_explicit_even_when_globally_enabled(
    harness, gate,
):
    service, account, session, row, preview = setup_borrower(harness)
    manager = service._plugin_manager
    if gate == "missing":
        delattr(manager, "enabled_for_identity")
    else:
        manager.enabled_for_identity = lambda *_args: False
    result = execute(service, row, preview)
    assert result["status"] == "blocked"
    assert result["provider_verified"] is False
    assert result["retry_borrow"] is False
    assert session.actions == 0
    assert service.storage.metadata_get(
        borrow_claim_key(account, row.notice),
    ) is None


@pytest.mark.parametrize("state", [
    "cancelled", "CANCELED", "refused", "rejected", "failed",
    "pending", "unknown", "closed", " returned ",
])
def test_explicit_non_active_loan_state_cannot_confirm_or_authorize_transfer(
    harness, state,
):
    service, account, session, row, preview = setup_borrower(
        harness, "state:" + state,
    )
    result = execute(service, row, preview)
    assert result["status"] == "verification_unavailable"
    assert result["provider_verified"] is False
    assert result["delivery"] == "unavailable"
    assert result["transfer"] == "blocked"
    assert result["retry_borrow"] is False
    assert session.actions == 1
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, row.notice),
    )["status"] == "verification_unavailable"
    assert execute(service, row, preview)["status"] == "blocked"
    assert session.actions == 1
