"""Core P0 #2055: no BnF borrowing POST without a live scope and durable claim.

Fixtures are offline. These tests never execute a provider borrow, token GET,
ADEPT or any remote action; a green result does not attest BnF eligibility.
"""
from __future__ import annotations

from dataclasses import replace
import json
from threading import Barrier, Thread

import pytest

from cifereca.models import SearchResult
from cifereca.core.borrow_action_scope import (
    borrow_claim_key, capture_borrow_action_scope,
)
from cifereca.storage import Storage
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def notice(account, *, resource_id="book-a"):
    return SearchResult(
        id="bnf-pret:" + resource_id, record_id=resource_id,
        docbase="BNF_PRET", medium="digital", title="Synthetic BnF book",
        author=None, type="Livre numérique", format=None,
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="BNF_PRET",
        source_provider="bnf-pret", source_credential=account.credential_id,
    )


def capture(service, session, account, item):
    return capture_borrow_action_scope(
        service, item, account, session, expected_identity_id="default",
    )


def test_exact_current_scoped_borrow_without_remote_read(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    item = notice(account, resource_id=loan.record_id)
    lease = capture(service, session, account, item)
    assert lease is not None and lease.current(service)
    key = borrow_claim_key(account, item)
    assert key.startswith("loan.borrow.v1:") and len(key) == len("loan.borrow.v1:") + 64
    assert loan.record_id not in key
    assert account.credential_id not in key
    assert not downloaded


@pytest.mark.parametrize("change", [
    "notice_id", "record_id", "provider", "credential", "docbase",
    "source_collection",
    "account_borrower", "account_identity", "account_primary",
    "account_credential", "account_provider", "session_credential",
    "session_replaced", "registered_account_replaced",
    "session_account_replaced", "session_account_duplicate",
    "core_epoch", "provider_epoch", "disconnected", "identity_aba",
])
def test_identity_or_selected_notice_change_revokes_scope(connected_core, change):
    service, session, account, loan, state, downloaded = connected_core
    item = notice(account)
    lease = capture(service, session, account, item)
    assert lease is not None
    if change == "notice_id":
        item.id = "bnf-pret:different"
    elif change == "record_id":
        item.record_id = "different"
    elif change == "provider":
        item.source_provider = "paris"
    elif change == "credential":
        item.source_credential = "other"
    elif change == "docbase":
        item.docbase = "OTHER"
    elif change == "source_collection":
        item.source_collection = "other-collection"
    elif change == "account_borrower":
        account.unique_id = "different"
    elif change == "account_identity":
        account.identity_id = "alex"
    elif change == "account_primary":
        account.is_primary = not account.is_primary
    elif change == "account_credential":
        account.credential_id = "other"
    elif change == "account_provider":
        account.provider_id = "paris"
    elif change == "session_credential":
        session.credential_id = "other"
    elif change == "session_replaced":
        service._sessions[service._session_key("bnf-pret", account.credential_id)] = object()
    elif change == "registered_account_replaced":
        service.accounts = [replace(account)]
    elif change == "session_account_replaced":
        session._accounts = [replace(account)]
    elif change == "session_account_duplicate":
        session._accounts = [account, account]
    elif change == "core_epoch":
        service._account_context_epoch += 1
    elif change == "provider_epoch":
        session._epoch = "synthetic-reconnect"
    elif change == "disconnected":
        session.connected = False
    elif change == "identity_aba":
        service.select_identity("alex")
        service.select_identity("default")
    assert lease.current(service) is False
    assert not downloaded


def test_borrow_scope_requires_exact_registered_source(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    assert capture(service, session, replace(account), notice(account)) is None
    assert capture(service, session, account, replace(notice(account), source_provider="kobo")) is None
    assert capture_borrow_action_scope(
        service, notice(account), account, session,
        expected_identity_id="alex",
    ) is None
    assert not downloaded


def test_unsafe_plugin_accounts_getter_is_not_invoked(connected_core, monkeypatch):
    service, session, account, loan, state, downloaded = connected_core
    hits = []

    def getter(_self):
        hits.append("forbidden")
        raise AssertionError("must never invoke provider properties")

    monkeypatch.setattr(type(session), "accounts", property(getter))
    assert capture(service, session, account, notice(account)) is None
    assert hits == []
    assert not downloaded


def test_key_changes_with_borrower_credential_identity_or_book(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    item = notice(account)
    key = borrow_claim_key(account, item)
    assert borrow_claim_key(replace(account, unique_id="other"), item) != key
    assert borrow_claim_key(replace(account, identity_id="alex"), item) != key
    assert borrow_claim_key(replace(account, credential_id="other"),
                            replace(item, source_credential="other")) != key
    # A UI/search row id is NOT a new right to submit another borrow POST
    # for the same upstream edition.
    assert borrow_claim_key(account, replace(item, id="other-ui-key")) == key
    assert borrow_claim_key(account, replace(
        item, source_collection="other-subcatalogue")) != key
    assert borrow_claim_key(account, replace(item, source_collection="")) is None
    assert borrow_claim_key(account, replace(item, record_id="other")) != key
    assert borrow_claim_key(account, replace(item, docbase="other")) != key
    assert borrow_claim_key(account, replace(item, medium="physical")) != key
    assert borrow_claim_key(account, replace(item, source_provider="paris")) is None
    assert borrow_claim_key(account, replace(item, record_id="")) is None
    assert not downloaded


def test_atomic_borrow_claim_blocks_other_threads_processes_and_restart(tmp_path):
    filename = tmp_path / "borrow.sqlite3"
    first, second = Storage(filename), Storage(filename)
    try:
        from cifereca.models import Account
        account = Account(provider_id="bnf-pret", credential_id="credential",
                          unique_id="reader", identity_id="default")
        key = borrow_claim_key(account, notice(account))
        barrier = Barrier(2)
        outcomes = []

        def race(storage, op):
            barrier.wait()
            outcomes.append(storage.loan_borrow_claim_once(key, op)[0])

        threads = [Thread(target=race, args=(storage, op))
                   for storage, op in ((first, "op-1"), (second, "op-2"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            assert not t.is_alive()
        assert sorted(outcomes) == [False, True]
        assert first.loan_borrow_state(key)["status"] == "request_sent"
        assert second.loan_borrow_claim_once(key, "retry")[0] is False
        first.close()
        reopened = Storage(filename)
        try:
            assert reopened.loan_borrow_claim_once(key, "restart")[0] is False
            assert reopened.loan_borrow_state(key)["status"] == "request_sent"
        finally:
            reopened.close()
    finally:
        second.close()


def test_borrow_finish_requires_one_operation_and_exact_positive_evidence(tmp_path):
    db = Storage(tmp_path / "borrow.sqlite3")
    try:
        from cifereca.models import Account
        account = Account(provider_id="bnf-pret", credential_id="credential",
                          unique_id="reader", identity_id="default")
        key = borrow_claim_key(account, notice(account))
        assert db.loan_borrow_claim_once(key, "op")[0]
        assert db.loan_borrow_finish(key, "wrong", status="confirmed",
                                     accepted=True, verified_loan_id="loan-a") is None
        with pytest.raises(ValueError, match="loan_borrow_transition_invalid"):
            db.loan_borrow_finish(key, "op", status="confirmed", accepted=True)
        with pytest.raises(ValueError, match="loan_borrow_transition_invalid"):
            db.loan_borrow_finish(key, "op", status="submitted", accepted=False)
        saved = db.loan_borrow_finish(key, "op", status="confirmed",
                                      accepted=True, verified_loan_id="loan-synthetic")
        assert saved["status"] == "confirmed"
        assert saved["verification"] == "positive_active_loan"
        assert db.loan_borrow_finish(key, "op", status="blocked") is None
        assert db.loan_borrow_claim_once(key, "repeat")[0] is False
        assert "Synthetic BnF book" not in json.dumps(saved)
    finally:
        db.close()


def test_malformed_borrow_checkpoint_never_reopened(tmp_path):
    db = Storage(tmp_path / "borrow.sqlite3")
    try:
        from cifereca.models import Account
        account = Account(provider_id="bnf-pret", credential_id="credential",
                          unique_id="reader", identity_id="default")
        key = borrow_claim_key(account, notice(account))
        db.metadata_set(key, "corrupt-before-restart")
        assert db.loan_borrow_state(key) is None
        assert db.loan_borrow_claim_once(key, "retry") == (
            False, {"status": "verification_unavailable"})
        for invalid in ("other:hash", "loan.borrow.v1:" + "Q" * 64, "", None):
            with pytest.raises(ValueError, match="loan_borrow_claim_invalid"):
                db.loan_borrow_claim_once(invalid, "op")
    finally:
        db.close()


def test_unsent_borrow_cas_release_allows_new_manual_attempt(tmp_path):
    """Read-only pre-POST rejection can release only its exact claim."""
    from cifereca.models import Account
    db = Storage(tmp_path / "unsent.sqlite3")
    account = Account(
        identity_id="default", provider_id="bnf-pret",
        credential_id="cred", unique_id="borrower",
    )
    key = borrow_claim_key(account, notice(account))
    try:
        claimed, row = db.loan_borrow_claim_once(key, "first-operation")
        assert claimed and row["status"] == "request_sent"
        assert not db.loan_borrow_release_unsent(key, "wrong-operation")
        assert db.loan_borrow_claim_once(key, "other")[0] is False
        assert db.loan_borrow_release_unsent(key, "first-operation") is True
        assert db.loan_borrow_state(key) is None
        assert db.loan_borrow_claim_once(key, "new-confirmed-action")[0] is True
        assert not db.loan_borrow_release_unsent(key, "first-operation")
        assert db.loan_borrow_state(key)["operation_id"] == "new-confirmed-action"
    finally:
        db.close()


def test_unsent_release_refuses_corrupt_confirmed_or_ambiguous_checkpoints(tmp_path):
    from cifereca.models import Account
    db = Storage(tmp_path / "unsent-refused.sqlite3")
    account = Account(
        identity_id="default", provider_id="bnf-pret",
        credential_id="cred", unique_id="borrower",
    )
    key = borrow_claim_key(account, notice(account))
    try:
        db.metadata_set(key, "corrupted")
        assert not db.loan_borrow_release_unsent(key, "op")
        assert db.metadata_get(key) == "corrupted"
        # A claim that may have attempted a POST cannot be deleted, even
        # when a caller has learned the exact operation id.
        db.metadata_set(key, "")
        assert not db.loan_borrow_release_unsent(key, "op")
        db.metadata_set(key, json.dumps({
            "schema": 1, "operation": "loan.borrow",
            "operation_id": "op", "status": "verification_unavailable",
            "request_accepted": False, "verification": "unavailable",
        }))
        assert not db.loan_borrow_release_unsent(key, "op")
        db.metadata_set(key, json.dumps({
            "schema": 1, "operation": "loan.borrow",
            "operation_id": "op", "status": "confirmed",
            "request_accepted": True, "verification": "positive_active_loan",
        }))
        assert not db.loan_borrow_release_unsent(key, "op")
        assert not db.loan_borrow_release_unsent("not-claim", "op")
        assert not db.loan_borrow_release_unsent(key, "")
        assert db.loan_borrow_claim_once(key, "retry")[0] is False
    finally:
        db.close()


def test_disabled_lender_connection_revokes_borrow_scope_without_provider_io(
    connected_core,
):
    service, session, account, loan, state, downloaded = connected_core
    item = notice(account)
    lease = capture(service, session, account, item)
    assert lease is not None
    service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": account.provider_id,
        "credential_id": account.credential_id, "label": "Disabled",
        "enabled": False, "metadata": {},
    })
    assert lease.current(service) is False
    assert capture(service, session, account, item) is None
    assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    assert not downloaded


def test_ambiguous_lender_binding_revokes_borrow_scope_before_claim(
    connected_core, monkeypatch,
):
    service, session, account, loan, state, downloaded = connected_core
    item = notice(account)
    lease = capture(service, session, account, item)
    assert lease is not None
    original = service.storage.provider_connections

    def ambiguous(*, identity_id):
        rows = original(identity_id=identity_id)
        if identity_id == "default":
            exact = next(row for row in rows
                         if row.get("credential_id") == account.credential_id)
            return rows + [{**exact, "enabled": False}]
        return rows

    monkeypatch.setattr(service.storage, "provider_connections", ambiguous)
    assert lease.current(service) is False
    assert capture(service, session, account, item) is None
    assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    assert not downloaded
