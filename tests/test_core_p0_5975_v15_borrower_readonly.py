"""#5975: historical Paris ADEPT rows are provenance, never a live grant."""
from dataclasses import replace

import pytest

from tests.historical_adept_seed import seed_historical_adept_binding
from tests.test_paris_borrower_adept_binding import (
    service_with_linked_borrower, confirm_borrower_v16,
)


@pytest.mark.parametrize("borrower", ["linked-123", "primary"])
def test_v15_only_cannot_select_adept_borrower(tmp_path, borrower):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id=borrower,
            borrower_identity_id="bob", confirmed=True,
        )
        observed = service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id=borrower,
        )
        assert observed["borrower_identity_id"] == "bob"
        assert service.storage.adept_borrower_grant_get(
            provider_id="paris", credential_id="alice-paris",
            account_id=borrower,
        ) is None
        assert service._paris_adept_identity_for_loan(
            replace(loan, source_account_id=borrower),
            connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
    finally:
        service.close()


def test_v15_only_automatic_acquisition_refuses_before_provider_call(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.acquire_digital_loan = lambda *_a, **_kw: pytest.fail(
            "v15-only must never reach the provider"
        )
        result = service._acquire_active_loan_automatically_guarded(loan)
        assert result["status"] == "attention_required"
        assert result["code"] == "adept_borrower_binding_required"
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()


def test_v16_is_only_positive_while_v15_remains_descriptive(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        confirm_borrower_v16(service)
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("bob", None)
        service.storage.adept_borrower_grant_delete(
            provider_id="paris", credential_id="alice-paris",
            account_id="linked-123",
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        archived = service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        )
        assert archived["borrower_identity_id"] == "bob"
    finally:
        service.close()
