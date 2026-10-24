"""P0 #2975: duplicate source loan IDs are not silent ownership choices.

The supplier can return pagination duplicates. Identical repeats are harmless;
two distinct editions, origins, titles or expiry dates for the SAME scoped
loan key are not independent purchases, not an attested return and not a
last-row-wins right. All tests use offline synthetic SQLite.
"""
from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from cifereca.core import library_mutation
from cifereca.core.sync_coordination import _opening_loans_scoped
from cifereca.models import Account
from tests.test_core_p0_2975_loan_absence_scope import _loan
from tests.test_sync_state_contract import _service


def _account():
    return Account(
        display_name="PRIVATE synthetic account", unique_id="reader",
        provider_id="paris", credential_id="cred", identity_id="default",
    )


def _rights(service):
    return service.storage.entitlements()


@pytest.mark.parametrize("field,changed", [
    ("record_id", "PRIVATE-other-edition"),
    ("due_at", "2098-01-01T00:00:00Z"),
    ("title", "PRIVATE-other-title"),
    ("provider", "SYRACUSE"),
    ("document_type", "another-kind"),
])
def test_source_scope_rejects_conflicting_duplicate_before_first_write(
    tmp_path, monkeypatch, field, changed,
):
    service = _service(tmp_path)
    try:
        account = _account()
        service.accounts = [account]
        initial = _loan("existing")
        service._persist_active_loans("default", [initial])
        before = _rights(service)
        first = _loan("duplicate")
        duplicate = replace(first, **{field: changed})
        assert _opening_loans_scoped(account, "default", [first])
        assert not _opening_loans_scoped(account, "default", [first, duplicate])
        # A malformed source is marked failed and NO positive rows from that
        # source reach the startup transaction or dashboard.
        monkeypatch.setattr(
            service, "_loans_for_account",
            lambda selected, force=False: [first, duplicate],
        )
        summary = service.opening_sync()
        owner = next(x for x in summary["identities"]
                     if x["identity_id"] == "default")
        assert owner["status"] == "failed"
        assert owner["realized"] == 0
        assert _rights(service) == before
        assert service.last_loans == []
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
        assert "PRIVATE-other-" not in json.dumps(summary)
    finally:
        service.close()


@pytest.mark.parametrize("field,changed", [
    ("record_id", "another-edition"),
    ("due_at", "2098-01-01T00:00:00Z"),
    ("provider", "SYRACUSE"),
    ("title", "PRIVATE conflicting"),
])
def test_direct_positive_writer_rejects_ambiguous_duplicate_atomically(
    tmp_path, field, changed,
):
    service = _service(tmp_path)
    try:
        existing = _loan("unrelated")
        library_mutation._persist_active_loans(
            service, "default", [existing], realize_expired=False,
        )
        before = _rights(service)
        first = _loan("duplicate")
        contradictory = replace(first, **{field: changed})
        with pytest.raises(ValueError, match="^loan_duplicate_ambiguous$"):
            library_mutation._persist_active_loans(
                service, "default", [first, contradictory], realize_expired=False,
            )
        assert _rights(service) == before
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
    finally:
        service.close()


def test_identical_repeated_page_row_is_persisted_once_and_idempotent(tmp_path):
    service = _service(tmp_path)
    try:
        account = _account()
        first = _loan("same")
        assert _opening_loans_scoped(account, "default", [first, replace(first)])
        outcome = library_mutation._persist_active_loans(
            service, "default", [first, replace(first)], realize_expired=False,
        )
        assert outcome == {
            "persisted": 1, "returned_realized": 0, "expired_realized": 0,
        }
        before = _rights(service)
        again = library_mutation._persist_active_loans(
            service, "default", [replace(first)], realize_expired=False,
        )
        assert again["persisted"] == 1
        assert len(_rights(service)) == 1
        assert _rights(service)[0]["identity_id"] == "default"
        assert _rights(service)[0]["provider_id"] == "paris"
        assert before[0]["entitlement_id"] == _rights(service)[0]["entitlement_id"]
    finally:
        service.close()


def test_same_loan_id_of_other_borrower_is_not_ambiguous(tmp_path):
    service = _service(tmp_path)
    try:
        first = _loan("shared", account="reader")
        second = replace(first, source_account_id="paired", record_id="different-edition")
        outcome = library_mutation._persist_active_loans(
            service, "default", [first, second], realize_expired=False,
        )
        assert outcome["persisted"] == 2
        assert {row["account_id"] for row in _rights(service)} == {"reader", "paired"}
    finally:
        service.close()
