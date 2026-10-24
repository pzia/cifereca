"""P0 #2975: provider loan provenance is never replaced by ADEPT/local profile."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core import library_mutation
from tests.test_core_p0_2975_loan_absence_scope import _loan
from tests.test_sync_state_contract import _service


@pytest.mark.parametrize("foreign_identity", ["alex", "", None])
def test_late_foreign_loan_identity_aborts_positive_batch_without_writes(
    tmp_path, foreign_identity,
):
    service = _service(tmp_path)
    try:
        valid = _loan("own", identity="default", provider="paris")
        foreign = replace(
            _loan("foreign", identity="alex", provider="paris"),
            identity_id=foreign_identity,
        )
        before = (
            service.storage.entitlements(),
            service.storage.acquisitions(),
            service.storage.placements(),
        )
        with pytest.raises(ValueError, match="^loan_identity_scope_mismatch$"):
            library_mutation._persist_active_loans(
                service, "default", [valid, foreign], realize_expired=False,
            )
        assert (
            service.storage.entitlements(),
            service.storage.acquisitions(),
            service.storage.placements(),
        ) == before
        assert service.last_loans == []
    finally:
        service.close()


def test_foreign_loan_cannot_refresh_existing_owned_row(tmp_path):
    service = _service(tmp_path)
    try:
        original = _loan("owned", identity="default")
        library_mutation._persist_active_loans(
            service, "default", [original], realize_expired=False,
        )
        before = service.storage.entitlements()
        foreign = replace(original, identity_id="alex", title="PRIVATE FOREIGN")
        with pytest.raises(ValueError, match="^loan_identity_scope_mismatch$"):
            library_mutation._persist_active_loans(
                service, "default", [foreign], realize_expired=False,
            )
        assert service.storage.entitlements() == before
    finally:
        service.close()


def test_upstream_site_is_provenance_not_adept_or_provider_display(tmp_path):
    service = _service(tmp_path)
    try:
        loan = _loan("site", identity="default", provider="paris")
        # The transport's display name (DILICOM) is not the owning source.
        assert loan.provider == "DILICOM"
        result = library_mutation._persist_active_loans(
            service, "default", [loan], realize_expired=False,
        )
        assert result["persisted"] == 1
        row, = service.storage.entitlements()
        assert row["provider_id"] == "paris"
        assert row["credential_id"] == loan.source_credential
        assert row["account_id"] == loan.source_account_id
        assert row["identity_id"] == "default"
        assert row["loan_id"] == loan.id
    finally:
        service.close()
