"""#4662: a confirmed lender loan never licenses a second profile to copy.

This is a Core+SQLite fixture, with no supplier, HTTP, download, ADEPT or
materialize_to_identity call. It observes only existing at-most-once claims.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.models import Account, SearchResult
from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.borrow_for_identity_progress import CrossProfileBorrowProgress
from tests.test_sync_state_contract import _service


@pytest.fixture
def setup(tmp_path):
    service = _service(tmp_path)
    account = Account(
        identity_id="alex", provider_id="paris",
        credential_id="paris-lender", unique_id="",
        is_primary=True, display_name="Bibliothèque",
    )
    notice = SearchResult(
        id="synthetic-row", record_id="digital-edition-1",
        docbase="DILICOM", medium="digital", title="Synthetic",
        author=None, type=None, format="EPUB", publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="EBOOKS", source_provider="paris",
        source_credential="paris-lender", source_collection="ebooks",
    )
    service.accounts = [account]
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": "paris-lender", "label": "Synthetic",
        "enabled": True, "metadata": {},
    })
    service.materialize_to_identity = lambda *_args, **_kwargs: (
        pytest.fail("claim observation must never copy another reader's bytes")
    )
    service.borrow_paris_manual = lambda *_args, **_kwargs: (
        pytest.fail("claim observation must never borrow from any lender")
    )
    try:
        yield service, account, notice
    finally:
        service.close()


def read(service, account, notice, requester="default"):
    return service.cross_profile_borrow_progress(
        requesting_identity_id=requester,
        borrowing_account=account, exact_notice=notice,
    )


def complete_claim(service, account, notice, operation="synthetic-operation"):
    key = borrow_claim_key(account, notice)
    claimed, _receipt = service.storage.loan_borrow_claim_once(key, operation)
    assert claimed is True
    row = service.storage.loan_borrow_finish(
        key, operation, status="confirmed",
        accepted=True, verified_loan_id="loan-exact",
    )
    assert row is not None and row["status"] == "confirmed"
    return key


def test_no_prior_loan_returns_no_transfer_and_never_mutates(setup):
    service, account, notice = setup
    progress = read(service, account, notice)
    assert progress == CrossProfileBorrowProgress(
        "not_attempted", "unavailable", "not_attempted",
        "loan_not_attempted",
    )
    assert progress.executable is progress.retry_borrow is False
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, notice)
    ) is None


def test_exact_lender_loan_confirmed_but_transfer_is_still_blocked(setup):
    service, account, notice = setup
    key = complete_claim(service, account, notice)
    progress = read(service, account, notice)
    assert progress.loan == "confirmed"
    assert progress.delivery == "unavailable"
    assert progress.transfer == "blocked"
    assert progress.reason_code == "lender_loan_confirmed_transfer_unverified"
    assert progress.executable is False
    assert progress.retry_borrow is False
    assert "loan-exact" not in repr(progress)
    assert "paris-lender" not in repr(progress)
    assert service.storage.loan_borrow_state(key)["status"] == "confirmed"
    assert service.storage.entitlements(provider_id="paris") == []


@pytest.mark.parametrize("status,accepted", [
    ("submitted", True),
    ("verification_unavailable", False),
    ("blocked", False),
])
def test_pending_uncertain_or_blocked_checkpoint_never_rearms_supplier(
    setup, status, accepted,
):
    service, account, notice = setup
    key = borrow_claim_key(account, notice)
    operation = "one-operation"
    assert service.storage.loan_borrow_claim_once(key, operation)[0]
    row = service.storage.loan_borrow_finish(
        key, operation, status=status, accepted=accepted,
    )
    assert row is not None
    verdict = read(service, account, notice)
    assert verdict.loan == "unknown"
    assert verdict.transfer == "blocked"
    assert verdict.retry_borrow is False
    assert service.storage.loan_borrow_claim_once(key, "forbidden-replay")[0] is False


def test_request_sent_is_not_treated_as_unattempted(setup):
    service, account, notice = setup
    key = borrow_claim_key(account, notice)
    assert service.storage.loan_borrow_claim_once(key, "in-flight")[0]
    result = read(service, account, notice)
    assert result.loan == "unknown"
    assert result.transfer == "blocked"
    assert result.retry_borrow is False


def test_corrupt_checkpoint_is_spent_not_a_new_borrow_opportunity(setup):
    service, account, notice = setup
    key = borrow_claim_key(account, notice)
    service.storage.metadata_set(key, "{corrupt")
    result = read(service, account, notice)
    assert result.loan == "unknown"
    assert result.transfer == "blocked"
    assert result.retry_borrow is False
    assert service.storage.loan_borrow_claim_once(key, "again")[0] is False


@pytest.mark.parametrize("mutation", [
    "wrong_requester", "same_identity", "foreign_source",
    "non_primary", "unknown_lender", "rebound_owner", "account_replaced",
    "physical_notice",
])
def test_wrong_lender_or_profile_scope_never_observes_foreign_claim(
    setup, mutation,
):
    service, account, notice = setup
    complete_claim(service, account, notice)
    requester = "default"
    if mutation == "wrong_requester":
        requester = "unknown"
    elif mutation == "same_identity":
        requester = "alex"
    elif mutation == "foreign_source":
        notice = replace(notice, source_credential="another")
    elif mutation == "non_primary":
        account.is_primary = False
    elif mutation == "unknown_lender":
        account.identity_id = "missing"
    elif mutation == "rebound_owner":
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "paris",
            "credential_id": "paris-lender", "label": "Rebound",
            "enabled": True, "metadata": {},
        })
    elif mutation == "account_replaced":
        account = replace(account)
    else:
        notice = replace(notice, medium="physical")
    result = read(service, account, notice, requester)
    assert result.reason_code == "lender_scope_unverified"
    assert result.loan == "unknown"
    assert result.transfer == "blocked"
    assert result.retry_borrow is False


@pytest.mark.parametrize("altered", ["edition", "collection", "borrower"])
def test_exact_claim_does_not_confirm_other_edition_collection_or_borrower(
    setup, altered,
):
    service, account, notice = setup
    complete_claim(service, account, notice)
    if altered == "edition":
        fresh_notice = replace(notice, record_id="other-edition")
        fresh_account = account
    elif altered == "collection":
        fresh_notice = replace(notice, source_collection="other-collection")
        fresh_account = account
    else:
        fresh_notice = notice
        fresh_account = replace(account, unique_id="other-borrower")
        service.accounts = [fresh_account]
    outcome = read(service, fresh_account, fresh_notice)
    assert outcome.loan == "not_attempted"
    assert outcome.transfer == "not_attempted"
    assert read(service, account, notice).loan == (
        "confirmed" if altered != "borrower" else "unknown"
    )



@pytest.mark.parametrize("mutation", [
    "disabled", "missing", "duplicate", "mixed_duplicate", "malformed",
])
def test_disabled_or_ambiguous_lender_connection_never_reads_private_claim(
    setup, monkeypatch, mutation,
):
    service, account, notice = setup
    complete_claim(service, account, notice)
    connections = service.storage.provider_connections
    if mutation == "disabled":
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "paris-lender", "label": "Synthetic",
            "enabled": False, "metadata": {},
        })
    elif mutation == "missing":
        monkeypatch.setattr(
            service.storage, "provider_connections", lambda **_kw: [],
        )
    elif mutation in ("duplicate", "mixed_duplicate"):
        original = connections(identity_id="alex")
        exact = next(row for row in original
                     if row.get("credential_id") == "paris-lender")
        duplicate = dict(exact)
        if mutation == "mixed_duplicate":
            duplicate["enabled"] = False
        monkeypatch.setattr(
            service.storage, "provider_connections",
            lambda **_kw: [*original, duplicate],
        )
    else:
        monkeypatch.setattr(
            service.storage, "provider_connections", lambda **_kw: (),
        )
    # A's UI must not even inspect B's durable at-most-once receipt if B's
    # registered connection is no longer uniquely enabled.
    monkeypatch.setattr(
        service.storage, "metadata_get",
        lambda *_args: pytest.fail("private lender metadata read after revocation"),
    )
    monkeypatch.setattr(
        service.storage, "loan_borrow_state",
        lambda *_args: pytest.fail("private lender receipt read after revocation"),
    )
    result = read(service, account, notice)
    assert result == CrossProfileBorrowProgress(
        "unknown", "unavailable", "blocked", "lender_scope_unverified",
    )


def test_reenabled_exact_lender_connection_can_again_observe_spent_receipt(setup):
    service, account, notice = setup
    complete_claim(service, account, notice)
    assert read(service, account, notice).loan == "confirmed"
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": "paris-lender", "label": "Synthetic",
        "enabled": False, "metadata": {},
    })
    assert read(service, account, notice).reason_code == "lender_scope_unverified"
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": "paris-lender", "label": "Synthetic",
        "enabled": True, "metadata": {},
    })
    result = read(service, account, notice)
    assert result.loan == "confirmed"
    assert result.transfer == "blocked" and result.retry_borrow is False


@pytest.mark.parametrize("phase", ["metadata", "checkpoint"])
def test_lender_binding_disabled_during_receipt_reads_never_publishes(
    setup, monkeypatch, phase,
):
    service, account, notice = setup
    complete_claim(service, account, notice)
    getter_name = "metadata_get" if phase == "metadata" else "loan_borrow_state"
    original = getattr(service.storage, getter_name)

    def revoke_while_reading(*args):
        value = original(*args)
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "paris-lender", "label": "Synthetic",
            "enabled": False, "metadata": {},
        })
        return value

    monkeypatch.setattr(service.storage, getter_name, revoke_while_reading)
    result = read(service, account, notice)
    assert result.loan == "unknown"
    assert result.transfer == "blocked"
    assert result.reason_code == "lender_scope_unverified"
    assert result.executable is False and result.retry_borrow is False
    # Historical lender claims remain spent: no second borrow authorization.
    assert service.storage.loan_borrow_claim_once(
        borrow_claim_key(account, notice), "replay-forbidden",
    )[0] is False
