"""P0 #5975: ADEPT historical return lookup never retargets a source or coerces row IDs.

This tests a pure provider-neutral scope plus the real LibraryService/SQLite
read-only status; the removed voluntary return is never exercised.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.core.adept_return_source_scope import HistoricalLoanSource
from cifereca.models import Loan
from cifereca.services import LibraryService
from tests.test_core_0984_adept_return_status_provenance import _acquisition
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def loan():
    return Loan(
        id="loan-1", record_id="item-1", provider="PNB",
        medium="digital", title="Exact read-only status",
        source_provider="paris", source_credential="card-a",
        source_account_id="linked-a", identity_id="alice",
    )


class Hostile:
    def __str__(self):
        raise AssertionError("must not stringify a plugin coordinate")

    def __bool__(self):
        raise AssertionError("must not test plugin coordinate truthiness")

    def __eq__(self, _other):
        raise AssertionError("must not compare untyped plugin coordinate")


class StrSubclass(str):
    def __eq__(self, _other):
        raise AssertionError("must not invoke overridden string comparison")


@pytest.mark.parametrize("field", [
    "source_provider", "source_credential", "source_account_id",
    "id", "record_id",
])
@pytest.mark.parametrize("malformed", [Hostile(), False, StrSubclass("loan-1")])
def test_malformed_source_fails_before_any_service_storage_read(field, malformed):
    target = loan()
    setattr(target, field, malformed)
    service = LibraryService.__new__(LibraryService)
    service.storage = NS(identity_for_connection=lambda *_a: pytest.fail(
        "untrusted source reached persisted owner lookup"
    ))
    assert HistoricalLoanSource.capture(target) is None
    assert service.adept_return_status_for_loan(target) == {
        "status": "adept_identity_unproven", "return_available": False,
    }


def test_no_dynamic_loan_like_object_can_select_a_historical_receipt():
    service = LibraryService.__new__(LibraryService)
    service.storage = NS(identity_for_connection=lambda *_a: pytest.fail(
        "untyped loan reached owner storage"
    ))
    candidate = NS(
        source_provider="paris", source_credential="card-a",
        source_account_id="linked-a", id="loan-1", record_id="item-1",
    )
    assert service.adept_return_status_for_loan(candidate) == {
        "status": "adept_identity_unproven", "return_available": False,
    }


def test_exact_account_and_item_id_never_coerce_missing_row_values():
    selected = loan()
    scope = HistoricalLoanSource.capture(selected)
    assert scope is not None
    assert scope.borrower_proven and scope.current(selected)
    row = {
        "provider_id": "paris", "credential_id": "card-a",
        "account_id": "linked-a", "item_id": "item-1",
        "identity_id": "alice", "loan_id": "loan-1",
        "entitlement_id": "right-1",
    }
    assert scope.matches_entitlement(row, identity_id="alice")
    assert scope.matches_acquisition(row, identity_id="alice")
    for changed in [
        {"account_id": None}, {"item_id": None},
        {"credential_id": "other"}, {"provider_id": "other"},
        {"identity_id": "bob"}, {"account_id": False},
        {"item_id": Hostile()}, {"entitlement_id": False},
        {"loan_id": Hostile()},
    ]:
        bad = {**row, **changed}
        assert not scope.matches_entitlement(bad, identity_id="alice")
        if "entitlement_id" not in changed and "loan_id" not in changed:
            assert not scope.matches_acquisition(bad, identity_id="alice")
    assert scope.matches_loan_receipt("loan-1")
    assert not scope.matches_loan_receipt(123)
    assert not scope.matches_loan_receipt(StrSubclass("loan-1"))


def test_known_owner_but_unknown_borrower_never_reads_private_ledger(tmp_path, monkeypatch):
    service, selected = service_with_linked_borrower(tmp_path)
    try:
        selected.source_account_id = None
        monkeypatch.setattr(service.storage, "entitlements", lambda **_kw: pytest.fail(
            "unknown borrower reached rights ledger"
        ))
        monkeypatch.setattr(service.storage, "acquisitions", lambda **_kw: pytest.fail(
            "unknown borrower reached acquisition ledger"
        ))
        assert service.adept_return_status_for_loan(selected) == {
            "status": "adept_identity_unproven", "return_available": False,
        }
    finally:
        service.close()


@pytest.mark.parametrize("mutation", [
    ("source_provider", "other"),
    ("source_credential", "card-other"),
    ("source_account_id", "other-reader"),
    ("id", "other-loan"),
    ("record_id", "other-edition"),
])
def test_borrower_callback_cannot_retarget_readonly_ledger(
    tmp_path, monkeypatch, mutation,
):
    service, selected = service_with_linked_borrower(tmp_path)
    try:
        def mutate(*_args, **_kwargs):
            setattr(selected, *mutation)
            return "bob", None

        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", mutate)
        monkeypatch.setattr(service.storage, "entitlements", lambda **_kw: pytest.fail(
            "stale borrower callback reached rights ledger"
        ))
        assert service.adept_return_status_for_loan(selected) == {
            "status": "adept_identity_unproven", "return_available": False,
        }
    finally:
        service.close()


@pytest.mark.parametrize("stage", ["rights", "acquisitions"])
def test_sqlite_read_callback_cannot_retarget_following_ledger_read(
    tmp_path, monkeypatch, stage,
):
    service, selected = service_with_linked_borrower(tmp_path)
    try:
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan",
                            lambda *_a, **_kw: ("bob", None))
        if stage == "rights":
            def mutated_rights(**_kw):
                selected.source_credential = "foreign"
                return []
            monkeypatch.setattr(service.storage, "entitlements", mutated_rights)
            monkeypatch.setattr(service.storage, "acquisitions",
                                lambda **_kw: pytest.fail("stale rights read must veto"))
        else:
            def mutated_acquisitions(**_kw):
                selected.source_account_id = "other-reader"
                return []
            monkeypatch.setattr(service.storage, "acquisitions", mutated_acquisitions)
        assert service.adept_return_status_for_loan(selected) == {
            "status": "adept_identity_unproven", "return_available": False,
        }
    finally:
        service.close()


@pytest.mark.parametrize("field,bad", [
    ("provider_id", "other"),
    ("credential_id", "other"),
    ("account_id", None),
    ("account_id", False),
    ("item_id", None),
    ("item_id", False),
])
def test_corrupt_acquisition_row_cannot_select_a_different_borrower(
    tmp_path, monkeypatch, field, bad,
):
    service, selected = service_with_linked_borrower(tmp_path)
    try:
        _acquisition(service, selected)
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan",
                            lambda *_a, **_kw: ("bob", None))
        rows = deepcopy(service.storage.acquisitions(provider_id="paris"))
        assert len(rows) == 1
        rows[0][field] = bad
        monkeypatch.setattr(service.storage, "acquisitions", lambda **_kw: rows)
        assert service.adept_return_status_for_loan(selected) == {
            "status": "no_acquisition", "return_available": False,
        }
    finally:
        service.close()


def test_numeric_historical_provider_loan_id_is_not_coerced(tmp_path, monkeypatch):
    service, selected = service_with_linked_borrower(tmp_path)
    try:
        selected.id = "123"
        acquisition_id = _acquisition(service, selected)
        row = service.storage.acquisition_get(acquisition_id)
        metadata = deepcopy(row["metadata"])
        metadata["provider_loan_id"] = 123
        service.storage.acquisition_put({**row, "metadata": metadata})
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan",
                            lambda *_a, **_kw: ("bob", None))
        # The existing cross-identity veto rejects malformed historical
        # receipt IDs before the narrower status selector can inspect them.
        # This is a valid fail-closed denial, never a coerced loan match.
        assert service.adept_return_status_for_loan(selected) == {
            "status": "adept_loan_identity_conflict", "return_available": False,
        }
    finally:
        service.close()
