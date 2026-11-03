"""P0 #3118: a loan auto-acquisition never borrows a foreign right ID.

The real read-only Core admission runs on disposable SQLite. A same-loan-ID
right from another borrower/credential/edition or a purchase has no authority
to decorate the new loan's receipt. No provider call or ADEPT execution.
"""
import pytest

from cifereca.core.loan_acquisition_admission import resolve
from tests.test_automatic_loan_admission import _setup, _forbid_work, LOGGER


def _right(service, loan, *, entitlement_id, **changes):
    row = {
        "entitlement_id": entitlement_id,
        "identity_id": "default",
        "provider_id": loan.source_provider,
        "credential_id": loan.source_credential,
        "account_id": loan.source_account_id,
        "item_id": loan.record_id,
        "entitlement_type": "loan",
        "state": "active",
        "loan_id": loan.id,
        "metadata": {"title": "synthetic right"},
    }
    row.update(changes)
    service.storage.entitlement_put(row)
    return entitlement_id


@pytest.mark.parametrize("wrong_scope", [
    {"credential_id": "foreign-credential"},
    {"account_id": "different-borrower"},
    {"item_id": "other-edition"},
    {"entitlement_type": "owned"},
])
def test_foreign_same_loan_id_never_becomes_auto_admission_entitlement(
    tmp_path, monkeypatch, wrong_scope,
):
    service, loan = _setup(tmp_path)
    try:
        _right(service, loan, entitlement_id="foreign", **wrong_scope)
        before = service.storage.entitlements(provider_id="paris", state="active")
        _forbid_work(service, monkeypatch)
        admitted = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert admitted.refusal is None
        assert admitted.identity.identity_id == "default"
        assert admitted.entitlement_id is None
        assert service.storage.entitlements(provider_id="paris", state="active") == before
        assert service.storage.acquisitions() == []
    finally:
        service.close()


@pytest.mark.parametrize("first", ["foreign", "valid"])
def test_exact_loan_entitlement_wins_even_when_foreign_right_is_first(
    tmp_path, monkeypatch, first,
):
    service, loan = _setup(tmp_path)
    try:
        creators = {
            "foreign": lambda: _right(
                service, loan, entitlement_id="foreign",
                account_id="other-borrower",
            ),
            "valid": lambda: _right(service, loan, entitlement_id="valid"),
        }
        creators[first]()
        creators["valid" if first == "foreign" else "foreign"]()
        before = service.storage.entitlements(provider_id="paris", state="active")
        _forbid_work(service, monkeypatch)
        admitted = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert admitted.refusal is None
        assert admitted.entitlement_id == "valid"
        assert service.storage.entitlements(provider_id="paris", state="active") == before
        assert service.storage.acquisitions() == []
    finally:
        service.close()


def test_multiple_exact_active_rights_do_not_get_arbitrarily_linked(
    tmp_path, monkeypatch,
):
    service, loan = _setup(tmp_path)
    try:
        _right(service, loan, entitlement_id="one")
        _right(service, loan, entitlement_id="two")
        before = service.storage.entitlements(provider_id="paris", state="active")
        _forbid_work(service, monkeypatch)
        admitted = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert admitted.refusal is None
        assert admitted.entitlement_id is None
        assert service.storage.entitlements(provider_id="paris", state="active") == before
    finally:
        service.close()
