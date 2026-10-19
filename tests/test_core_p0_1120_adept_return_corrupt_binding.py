"""P0 #1120: legacy/corrupted ADEPT metadata never crashes return status.

Only existing local acquisitions/SQLite; no Adobe/Paris network requests,
no return mutation, no download or local artifact modification.
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tests.test_core_0984_adept_first_paris_reconcile import setup


PRIVATE = "https://private.invalid/adept?auth=PRIVATE_ACCOUNT_TOKEN_2026"


@pytest.mark.parametrize(("mutation", "expected_status"), [
    ({"return_verification": [PRIVATE]}, "verification_failed"),
    ({"inventory_status": {PRIVATE: "unexpected"}}, "verification_failed"),
    ({"loan_state": [PRIVATE]}, "verification_failed"),
    ({"return_available": "yes"}, "ready"),
    ({"return_available": 1}, "ready"),
])
def test_corrupt_binding_fields_block_return_without_reading_provider(
    tmp_path, monkeypatch, mutation, expected_status,
):
    service, loan, paris = setup(tmp_path)
    try:
        row = service.storage.acquisition_get("adept-01")
        row["metadata"]["adept_binding"].update(mutation)
        service.storage.acquisition_put(row)
        before = deepcopy(service.storage.acquisition_get("adept-01"))
        monkeypatch.setattr(
            service, "_kobo_adapter",
            lambda: pytest.fail("Return status must not fetch Adobe inventory"),
        )
        monkeypatch.setattr(
            service, "execute_drm_mutation",
            lambda *_a: pytest.fail("Return status must not mutate Adobe"),
        )
        observed = service.adept_return_status_for_loan(loan)
        assert observed["status"] == expected_status
        assert observed["return_available"] is False
        assert "PRIVATE_ACCOUNT_TOKEN_2026" not in json.dumps(observed)
        assert paris.posts == 0 and paris.accounts_read == []
        assert service.storage.acquisition_get("adept-01") == before
    finally:
        service.close()


@pytest.mark.parametrize("malformed", [
    [PRIVATE], PRIVATE, 1, True,
])
def test_non_mapping_adept_binding_cannot_offer_return(tmp_path, malformed):
    service, loan, paris = setup(tmp_path)
    try:
        row = service.storage.acquisition_get("adept-01")
        row["metadata"]["adept_binding"] = malformed
        service.storage.acquisition_put(row)
        status = service.adept_return_status_for_loan(loan)
        assert status["status"] == "loan_id_missing"
        assert status["return_available"] is False
        assert "PRIVATE_ACCOUNT_TOKEN_2026" not in json.dumps(status)
        assert paris.posts == 0
    finally:
        service.close()


def test_valid_historical_ready_state_still_allows_explicit_return(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        state = service.adept_return_status_for_loan(loan)
        assert state["status"] == "ready"
        assert state["return_available"] is True
        assert state["acquisition_id"] == "adept-01"
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()
