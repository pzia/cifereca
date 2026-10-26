"""P0 #2975: Paris primary borrower errors must not poison sibling sources.

All loans and acquisitions are synthetic. No PNB/ADEPT request or download.
"""
from __future__ import annotations

import json

import pytest

from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.models import Account, Loan
from tests.test_core_p0_1346_opening_source_isolation import _state
from tests.test_sync_state_contract import _service


PRIVATE = "https://private.invalid/reader.acsm?sig=DO_NOT_REPORT"


def _loan(account, label):
    return Loan(
        id="loan-" + label, provider="DILICOM", medium="digital",
        record_id="edition-" + label, title="Synthetic " + label,
        source_provider="paris", source_credential="same-credential",
        source_account_id=account or "primary", identity_id="default",
        ebook_url=PRIVATE, due_at="2099-01-01T00:00:00Z",
    )


@pytest.mark.parametrize("failed_borrower", ["", "paired"])
def test_opening_acquisition_error_is_exact_paris_borrower_only(
    tmp_path, monkeypatch, failed_borrower,
):
    service = _service(tmp_path)
    try:
        primary = Account(
            display_name="PRIVATE primary", unique_id="", is_primary=True,
            provider_id="paris", credential_id="same-credential",
            identity_id="default",
        )
        paired = Account(
            display_name="PRIVATE paired", unique_id="paired", is_primary=False,
            provider_id="paris", credential_id="same-credential",
            identity_id="default",
        )
        kobo = Account(
            display_name="PRIVATE kobo", unique_id="kobo-owner",
            provider_id="kobo", credential_id="kobo-credential",
            identity_id="default",
        )
        other_identity = Account(
            display_name="PRIVATE alex", unique_id="another-borrower",
            provider_id="paris", credential_id="another-credential",
            identity_id="alex",
        )
        service.accounts = [primary, paired, kobo, other_identity]
        observed = {
            "": [_loan("", "primary")],
            "paired": [_loan("paired", "paired")],
        }
        def read(account, *, force=False):
            assert force is True
            return observed.get(account.unique_id, [])

        seen = []
        def acquire(loan):
            seen.append(loan.source_account_id)
            return (
                {"status": "attention_required", "code": "attention_required"}
                if loan.source_account_id == (failed_borrower or "primary")
                else {"status": "complete"}
            )

        monkeypatch.setattr(service, "_loans_for_account", read)
        monkeypatch.setattr(service, "_acquire_active_loan_automatically", acquire)
        enable_synthetic_paris_opening(service)
        summary = service.opening_sync()
        assert sorted(seen) == ["paired", "primary"]
        by_identity = {entry["identity_id"]: entry for entry in summary["identities"]}
        assert by_identity["default"]["status"] == "partial"
        assert by_identity["default"]["attention_required"] == 1
        assert by_identity["alex"]["status"] == "success"
        target = primary if failed_borrower == "" else paired
        healthy = paired if failed_borrower == "" else primary
        assert _state(service, target)["status"] == "partial"
        assert _state(service, healthy)["status"] == "success"
        assert _state(service, healthy)["last_success"]
        assert _state(service, kobo)["status"] == "success"
        assert _state(service, other_identity)["status"] == "success"
        assert len(service.storage.entitlements(provider_id="paris")) == 2
        for entry in service.storage.entitlements(provider_id="paris"):
            assert entry["state"] == "active"
        view = json.dumps(summary)
        assert PRIVATE not in view
        assert "same-credential" not in view
        assert "PRIVATE paired" not in service.diagnostics.text("test")
    finally:
        service.close()


def test_unknown_post_fetch_origin_degrades_all_sources_instead_of_guessing(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        primary = Account(
            unique_id="", provider_id="paris",
            credential_id="same-credential", identity_id="default",
        )
        sibling = Account(
            unique_id="paired", is_primary=False, provider_id="paris",
            credential_id="same-credential", identity_id="default",
        )
        service.accounts = [primary, sibling]
        loan = _loan("", "primary")
        monkeypatch.setattr(
            service, "_loans_for_account",
            lambda account, force=False: [loan] if account is primary else [],
        )
        def mutate_loan_on_acquisition(row):
            # Simulate a stale/mutated provider row while the Core is
            # attempting acquisition. Never credit the result to a guess.
            row.source_account_id = "unknown-third-account"
            return {"status": "retryable_error"}

        monkeypatch.setattr(
            service, "_acquire_active_loan_automatically",
            mutate_loan_on_acquisition,
        )
        enable_synthetic_paris_opening(service)
        result = service.opening_sync()
        assert next(x for x in result["identities"]
                    if x["identity_id"] == "default")["status"] == "partial"
        assert _state(service, primary)["status"] == "partial"
        assert _state(service, sibling)["status"] == "partial"
    finally:
        service.close()
