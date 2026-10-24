"""P0 #2975: Paris's "primary" sentinel needs a genuine primary borrower.

The same exact borrower rule protects positive sync rows and the negative
inventory re-read used after PNB/ADEPT return. Empty lists without a valid
borrower identity must NEVER prove a returned digital loan.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.loan_return_verification import _paris_inventory_exact_borrower
from cifereca.core.sync_coordination import _opening_loans_scoped
from cifereca.models import Account
from tests.test_core_p0_2975_loan_absence_scope import _loan
from tests.test_sync_state_contract import _service


def _account(uid, primary):
    return Account(
        display_name="PRIVATE synthetic borrower",
        unique_id=uid,
        is_primary=primary,
        provider_id="paris",
        credential_id="credential",
        identity_id="default",
    )


@pytest.mark.parametrize("uid,primary", [
    ("", False), ("primary", False), (None, False), (0, True),
])
def test_unbound_secondary_never_claims_primary_even_for_empty_inventory(
    uid, primary,
):
    account = _account(uid, primary)
    row = _loan("borrowed", credential="credential", account="primary")
    assert not _opening_loans_scoped(account, "default", [row])
    assert not _opening_loans_scoped(account, "default", [])
    # This path decides whether absence of a PNB loan is provider-attested.
    assert not _paris_inventory_exact_borrower([row], account)
    assert not _paris_inventory_exact_borrower([], account)


def test_true_primary_sentinel_and_distinct_paired_id_are_supported():
    primary = _account("", True)
    paired = _account("paired-uid", False)
    primary_row = _loan("P", credential="credential", account="primary")
    paired_row = _loan("S", credential="credential", account="paired-uid")
    assert _opening_loans_scoped(primary, "default", [primary_row])
    assert _opening_loans_scoped(paired, "default", [paired_row])
    assert _paris_inventory_exact_borrower([primary_row], primary)
    assert _paris_inventory_exact_borrower([paired_row], paired)
    assert _paris_inventory_exact_borrower([], primary)
    assert _paris_inventory_exact_borrower([], paired)
    assert not _opening_loans_scoped(primary, "default", [paired_row])
    assert not _paris_inventory_exact_borrower([paired_row], primary)


def test_invalid_secondary_alias_cannot_publish_positive_startup_rights(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        old = _loan("old", credential="credential", account="primary")
        service._persist_active_loans("default", [old])
        before = service.storage.entitlements()
        account = _account("", False)
        service.accounts = [account]
        candidate = _loan("new", credential="credential", account="primary")
        monkeypatch.setattr(
            service, "_loans_for_account",
            lambda selected, force=False: [candidate],
        )
        report = service.opening_sync()
        active = next(
            row for row in report["identities"]
            if row["identity_id"] == "default"
        )
        assert active["status"] == "failed"
        assert active["realized"] == 0
        assert service.storage.entitlements() == before
        assert service.last_loans == []
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
    finally:
        service.close()


def test_only_true_primary_empty_uid_can_match_legacy_provider_loan():
    primary = _account("", True)
    illegal = replace(primary, is_primary=False)
    loan = _loan("legacy", credential="credential", account="primary")
    # Legacy Paris rows can have identity_id=None and are still scoped by
    # the exact account/credential. Invalid account shape remains unsafe.
    legacy = replace(loan, identity_id=None)
    assert _paris_inventory_exact_borrower([legacy], primary)
    assert not _paris_inventory_exact_borrower([legacy], illegal)
