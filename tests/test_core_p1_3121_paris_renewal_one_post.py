"""P1 #3121: exact consented Paris RenewLoans, durable at-most-once.

All sessions and HTTP results are synthetic. Never call a real lender,
ADEPT, ACSM endpoint, filesystem publication or a remote return/borrow.
"""
from __future__ import annotations

from dataclasses import replace
import json
from threading import Barrier, Thread

import pytest

from cifereca.models import Account, Loan
from cifereca.providers.base import (
    ProviderCapabilities, ProviderDescriptor, ProviderSession,
)
from cifereca.core.provider_loan_renewal import _source_key
from cifereca.gui.loan_renewal_feedback import loan_renewal_feedback
from tests.test_sync_state_contract import _service


def account(*, credential="card", borrower="reader", identity="default"):
    return Account(
        provider_id="paris", credential_id=credential, unique_id=borrower,
        identity_id=identity, is_primary=not bool(borrower),
    )


def loan(a, *, loan_id="loan-1", record_id="edition-1", branch="SYRACUSE",
         can_renew=True, raw=False, state="active"):
    return Loan(
        id=loan_id, record_id=record_id, title="Private synthetic book",
        provider=branch,
        medium="physical" if branch == "SYRACUSE" else "digital",
        state=state,
        source_provider="paris", source_credential=a.credential_id,
        source_account_id=a.unique_id or "primary",
        identity_id=a.identity_id,
        renewable=can_renew,
        due_at="2099-01-01T00:00:00Z",
        raw=({"Id": loan_id, "RecordId": record_id, "CanRenew": can_renew}
             if raw else {}),
    )


class FakeParis(ProviderSession):
    def __init__(self, a, inventory):
        descriptor = ProviderDescriptor(
            id="paris", display_name="Synthetic Paris", description="Test",
            state="ready",
            capabilities=ProviderCapabilities(loans=True, renew_loan=True),
        )
        super().__init__(
            descriptor=descriptor, credential_id=a.credential_id, label="Paris",
            credentials={}, options={},
        )
        self.connected = True
        self._accounts = [a]
        self.inventory = list(inventory)
        self.posts = []
        self.reads = []
        self.result = {"Renewed": True}
        self.failure = None
        self.during_read = lambda: None
        self.during_post = lambda: None

    def connect(self):
        raise AssertionError("synthetic renew must not login")

    def close(self):
        self.connected = False

    def loans(self, account):
        self.reads.append(account.unique_id)
        self.during_read()
        return list(self.inventory)

    def renew(self, row, account):
        self.posts.append((row.id, row.provider, account.unique_id, row.raw))
        self.during_post()
        if self.failure is not None:
            raise self.failure
        return dict(self.result)


def setup(tmp_path, *, selected=None, upstream=None):
    service = _service(tmp_path)
    a = account()
    ui = selected if selected is not None else loan(a)
    fresh = upstream if upstream is not None else loan(a, raw=True)
    session = FakeParis(a, [fresh])
    service.accounts = [a]
    service._sessions[service._session_key("paris", a.credential_id)] = session
    service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": "paris",
        "credential_id": a.credential_id, "label": "Synthetic Paris",
        "enabled": True, "metadata": {},
    })
    return service, a, ui, session


def test_consent_is_strict_and_precedes_any_loan_read_or_claim(tmp_path):
    service, a, ui, session = setup(tmp_path)
    try:
        for consent in (False, None, 1, "yes"):
            outcome = service.renew(ui, confirmed=consent)
            assert outcome["status"] == "blocked"
            assert outcome["code"] == "renewal_confirmation_required"
        assert session.reads == session.posts == []
        assert service.storage.loan_renewal_state(_source_key(ui)) is None
    finally:
        service.close()


def test_fresh_borrower_exact_positive_loan_posts_once_and_never_claims_due_date(
    tmp_path,
):
    service, a, ui, session = setup(tmp_path)
    try:
        ui.raw["Untrusted"] = "must never be sent"
        result = service.renew(ui, confirmed=True)
        assert result["status"] == "submitted"
        assert result["provider_request_accepted"] is True
        assert result["provider_verified"] is False
        assert session.reads == ["reader"]
        assert session.posts == [(
            "loan-1", "SYRACUSE", "reader",
            {"Id": "loan-1", "RecordId": "edition-1", "CanRenew": True},
        )]
        assert service.storage.loan_renewal_state(_source_key(ui))["status"] == "submitted"
        assert service.renew(ui, confirmed=True)["status"] == "submitted"
        assert len(session.posts) == len(session.reads) == 1
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
        assert not service.storage.entitlements(provider_id="paris")
        assert "Private synthetic book" not in json.dumps(result)
    finally:
        service.close()


@pytest.mark.parametrize("failure", [
    TimeoutError("sensitive TOKEN=SECRET"), ValueError("unexpected"),
])
def test_uncertain_post_never_replays_even_after_restart(tmp_path, failure):
    service, a, ui, session = setup(tmp_path)
    try:
        session.failure = failure
        result = service.renew(ui, confirmed=True)
        assert result["status"] == "verification_unavailable"
        assert result["provider_verified"] is False
        assert len(session.posts) == 1
        assert service.renew(ui, confirmed=True)["status"] == "verification_unavailable"
        assert len(session.posts) == 1
        assert "SECRET" not in json.dumps(result)
        assert "SECRET" not in json.dumps(
            service.storage.loan_renewal_state(_source_key(ui))
        )
    finally:
        service.close()
    reopened, a, _, other_session = setup(tmp_path)
    try:
        repeat = reopened.renew(ui, confirmed=True)
        assert repeat["status"] == "verification_unavailable"
        assert other_session.reads == other_session.posts == []
    finally:
        reopened.close()


@pytest.mark.parametrize("change", [
    "foreign_borrower", "foreign_credential", "foreign_identity",
    "foreign_branch", "foreign_edition", "duplicate_id",
    "missing_raw", "not_renewable", "unknown_loan",
])
def test_mismatched_or_unverified_fresh_loan_refuses_before_claim(tmp_path, change):
    service, a, ui, session = setup(tmp_path)
    try:
        original = session.inventory[0]
        if change == "foreign_borrower":
            session.inventory = [replace(original, source_account_id="other")]
        elif change == "foreign_credential":
            session.inventory = [replace(original, source_credential="other")]
        elif change == "foreign_identity":
            session.inventory = [replace(original, identity_id="alex")]
        elif change == "foreign_branch":
            session.inventory = [replace(
                original, provider="DILICOM", medium="digital",
            )]
        elif change == "foreign_edition":
            session.inventory = [replace(original, record_id="other")]
        elif change == "duplicate_id":
            session.inventory.append(replace(
                original, provider="DILICOM", medium="digital",
            ))
        elif change == "missing_raw":
            session.inventory = [replace(original, raw={})]
        elif change == "not_renewable":
            session.inventory = [replace(
                original, renewable=False,
                raw={"Id": "loan-1", "CanRenew": False},
            )]
        else:
            session.inventory = []
        result = service.renew(ui, confirmed=True)
        assert result["status"] == "blocked"
        assert session.posts == []
        assert service.storage.loan_renewal_state(_source_key(ui)) is None
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    "session_swapped", "account_replaced", "borrower_changed",
    "identity_aba", "credential_changed",
])
def test_lender_read_context_mutation_disallows_renewal_post(tmp_path, change):
    service, a, ui, session = setup(tmp_path)
    try:
        def attack():
            if change == "session_swapped":
                replacement = FakeParis(a, [])
                service._sessions[service._session_key("paris", "card")] = replacement
            elif change == "account_replaced":
                service.accounts = [replace(a)]
            elif change == "borrower_changed":
                a.unique_id = "other"
            elif change == "credential_changed":
                session.credential_id = "other"
            elif change == "identity_aba":
                service.select_identity("alex")
                service.select_identity("default")
        session.during_read = attack
        result = service.renew(ui, confirmed=True)
        assert result["status"] == "blocked"
        assert session.posts == []
        assert service.storage.loan_renewal_state(_source_key(ui)) is None
    finally:
        service.close()


@pytest.mark.parametrize("change", ["account_replaced", "identity_aba", "session_closed"])
def test_after_post_context_revocation_is_never_confirmed_or_replayed(
    tmp_path, change,
):
    service, a, ui, session = setup(tmp_path)
    try:
        def attack():
            if change == "account_replaced":
                service.accounts = [replace(a)]
            elif change == "identity_aba":
                service.select_identity("alex")
                service.select_identity("default")
            elif change == "session_closed":
                session.connected = False
        session.during_post = attack
        result = service.renew(ui, confirmed=True)
        assert result["status"] == "verification_unavailable"
        assert len(session.posts) == 1
        assert service.storage.loan_renewal_state(_source_key(ui)) is not None
        service.renew(ui, confirmed=True)
        assert len(session.posts) == 1
    finally:
        service.close()


def test_atomic_renewal_claim_has_exactly_one_cross_thread_winner(tmp_path):
    service, a, ui, session = setup(tmp_path)
    key = _source_key(ui)
    winners = []
    barrier = Barrier(2)

    def worker(index):
        barrier.wait()
        winners.append(service.storage.loan_renewal_claim_once(
            key, "operation-" + str(index),
        )[0])

    threads = [Thread(target=worker, args=(i,)) for i in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
        assert sorted(winners) == [False, True]
        assert service.storage.loan_renewal_state(key)["status"] == "request_sent"
        assert not session.posts
    finally:
        service.close()


def test_renewal_feedback_never_calls_submitted_a_confirmed_new_due_date():
    submitted = loan_renewal_feedback({
        "status": "submitted", "code": "renewal_provider_submitted",
        "provider_request_accepted": True, "provider_verified": False,
    })
    assert submitted[0] == "submitted"
    assert "pas encore confirmée" in submitted[1]
    assert "prolongé" not in submitted[1].casefold()
    ambiguous = loan_renewal_feedback({
        "status": "submitted", "code": "SECRET URL",
        "provider_request_accepted": True, "provider_verified": True,
    })
    assert ambiguous[0] == "attention"
    assert "SECRET" not in str(ambiguous)
    assert loan_renewal_feedback({
        "status": "verification_unavailable",
        "code": "renewal_provider_response_unverified",
    })[0] == "attention"


def test_gui_renewal_never_proclaims_success_from_unverified_post():
    from pathlib import Path
    src = (
        Path(__file__).parents[1]
        / "src/cifereca/gui/loans_controller.py"
    ).read_text(encoding="utf-8")
    block = src.split("def _renew_result(", 1)[1].split("def confirm_return(", 1)[0]
    assert "loan_renewal_feedback(result)" in block
    assert "self.service.renew(loan, confirmed=True)" in block
    assert "success_toast=" not in block
    assert "Prêt prolongé" not in block


def test_context_revoked_after_sqlite_claim_never_dispatches_and_cannot_retry(
    tmp_path, monkeypatch,
):
    service, a, ui, session = setup(tmp_path)
    real_claim = service.storage.loan_renewal_claim_once

    def claim_then_revoke(key, operation_id):
        claimed, row = real_claim(key, operation_id)
        if claimed:
            service._account_context_epoch += 1
        return claimed, row

    monkeypatch.setattr(
        service.storage, "loan_renewal_claim_once", claim_then_revoke,
    )
    try:
        result = service.renew(ui, confirmed=True)
        assert result["status"] == "blocked"
        assert session.posts == []
        assert service.storage.loan_renewal_state(_source_key(ui))["status"] == "blocked"
        # The persisted claim prevents replay on another process after any
        # uncertain failure between the SQLite commit and RenewLoans dispatch.
        assert service.renew(ui, confirmed=True)["status"] == "verification_unavailable"
        assert session.posts == []
    finally:
        service.close()


def test_capability_absent_and_wrong_selected_identity_never_renew(tmp_path):
    service, a, ui, session = setup(tmp_path)
    try:
        from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
        session.descriptor = replace(
            session.descriptor,
            capabilities=ProviderCapabilities(loans=True, renew_loan=False),
        )
        assert service.renew(ui, confirmed=True)["status"] == "blocked"
        assert session.reads == session.posts == []
        session.descriptor = replace(
            session.descriptor,
            capabilities=ProviderCapabilities(loans=True, renew_loan=True),
        )
        service.select_identity("alex")
        assert service.renew(ui, confirmed=True)["status"] == "blocked"
        assert session.reads == session.posts == []
        assert service.storage.loan_renewal_state(_source_key(ui)) is None
    finally:
        service.close()


@pytest.mark.parametrize("response", [
    {"Renewed": False, "success": True},
    {"Renewed": 1},
    {"success": 1},
    {"Renewed": False},
    {},
])
def test_malformed_or_contradictory_post_ack_never_claims_submission(
    tmp_path, response,
):
    service, a, ui, session = setup(tmp_path)
    try:
        session.result = response
        outcome = service.renew(ui, confirmed=True)
        assert outcome["status"] == "verification_unavailable"
        assert outcome["provider_verified"] is False
        assert len(session.posts) == 1
        assert service.renew(ui, confirmed=True)["status"] == "verification_unavailable"
        assert len(session.posts) == 1
    finally:
        service.close()


def test_renewal_state_never_interferes_with_return_checkpoint_namespace(tmp_path):
    from cifereca.core.loan_return_guard import return_key
    service, a, ui, session = setup(tmp_path)
    try:
        prior_return = return_key("paris", "card", "reader", "loan-1")
        assert service.storage.loan_return_state(prior_return) is None
        assert service.renew(ui, confirmed=True)["status"] == "submitted"
        assert service.storage.loan_return_state(prior_return) is None
        assert service.storage.loan_renewal_state(_source_key(ui)) is not None
    finally:
        service.close()
