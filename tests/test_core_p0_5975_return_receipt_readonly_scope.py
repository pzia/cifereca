"""P0 #5975: historical return receipt projection cannot infer borrower scope."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_return_guard import (
    read_only_return_receipt_key, return_key,
)
from cifereca.services import LibraryService


class Exploding:
    def __str__(self):
        raise AssertionError("no coercion of private loan metadata")

    def __bool__(self):
        raise AssertionError("no truthiness of plugin loan metadata")

    def __hash__(self):
        raise AssertionError("no hashing of plugin loan metadata")


def loan(**overrides):
    fields = {
        "source_provider": "paris",
        "source_credential": "credential-a",
        "source_account_id": "borrower-a",
        "id": "loan-a",
    }
    fields.update(overrides)
    return NS(**fields)


def test_exact_receipt_key_and_historical_empty_primary_compatibility():
    assert read_only_return_receipt_key(loan()) == return_key(
        "paris", "credential-a", "borrower-a", "loan-a",
    )
    assert read_only_return_receipt_key(
        loan(source_account_id=""),
    ) == return_key("paris", "credential-a", "primary", "loan-a")
    assert read_only_return_receipt_key(
        loan(source_account_id="primary"),
    ) == return_key("paris", "credential-a", "primary", "loan-a")


@pytest.mark.parametrize("field", [
    "source_provider", "source_credential", "source_account_id", "id",
])
@pytest.mark.parametrize("invalid", [None, False, 0, Exploding()])
def test_invalid_native_field_cannot_address_receipt(field, invalid):
    assert read_only_return_receipt_key(loan(**{field: invalid})) is None


@pytest.mark.parametrize("field", [
    "source_provider", "source_credential", "id",
])
def test_empty_required_field_cannot_address_receipt(field):
    assert read_only_return_receipt_key(loan(**{field: ""})) is None


def test_missing_borrower_does_not_fall_back_to_primary():
    assert read_only_return_receipt_key(
        loan(source_account_id=None),
    ) is None


def test_service_projection_never_reads_storage_for_invalid_loan():
    service = LibraryService.__new__(LibraryService)
    service.storage = NS(loan_return_state=lambda _key: pytest.fail(
        "unproven borrower must not read private return checkpoint"
    ))
    for candidate in [
        loan(source_account_id=None),
        loan(source_provider=Exploding()),
        loan(id=Exploding()),
    ]:
        assert service.loan_return_state(candidate) is None


def test_service_projection_preserves_exact_receipt_and_public_sanitizer():
    key = return_key("paris", "credential-a", "borrower-a", "loan-a")
    calls = []
    service = LibraryService.__new__(LibraryService)
    def lookup(requested):
        calls.append(requested)
        return {"status": "still_active", "verification": "still_present"}
    service.storage = NS(loan_return_state=lookup)
    public = service.loan_return_state(loan())
    assert calls == [key]
    assert public["status"] == "still_active"
    assert public["verification"] == "still_present"
