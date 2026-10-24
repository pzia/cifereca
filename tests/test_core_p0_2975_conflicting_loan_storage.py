"""P0 #2975: a supplier's repeated loan ID cannot mask a conflicting edition.

Synthetic borrower/source observations only. No provider traffic, ADEPT,
downloads, placement writes, metadata logging or actual loan returns.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.acquisition_engine import Entitlement, SourceIdentity
from cifereca.core import library_mutation
from tests.test_core_p0_2975_loan_absence_scope import _loan
from tests.test_sync_state_contract import _service


def _rights(service):
    return service.storage.entitlements()


@pytest.mark.parametrize("conflict", ["edition", "expiry"])
def test_direct_positive_batch_rejects_late_conflicting_loan_id_without_writes(
    tmp_path, conflict,
):
    service = _service(tmp_path)
    try:
        first = _loan("healthy", account="reader")
        second = _loan("repeated", account="reader")
        if conflict == "edition":
            repeated = replace(second, record_id="PRIVATE_OTHER_EDITION")
        else:
            repeated = replace(second, due_at="2088-02-02T00:00:00Z")
        with pytest.raises(ValueError, match="^loan_source_duplicate_conflict$"):
            library_mutation._persist_active_loans(
                service, "default", [first, second, repeated],
                realize_expired=False,
            )
        assert _rights(service) == []
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
        assert "PRIVATE_OTHER_EDITION" not in json.dumps(_rights(service))
        # A corrected provider snapshot may be persisted normally.
        assert library_mutation._persist_active_loans(
            service, "default", [first, second], realize_expired=False,
        )["persisted"] == 2
    finally:
        service.close()


def test_identical_loan_page_duplicate_is_one_positive_right(tmp_path):
    service = _service(tmp_path)
    try:
        first = _loan("repeated")
        again = replace(first, title="Second page title")
        outcome = library_mutation._persist_active_loans(
            service, "default", [first, again], realize_expired=False,
        )
        assert outcome == {
            "persisted": 1, "returned_realized": 0, "expired_realized": 0,
        }
        row, = _rights(service)
        assert row["metadata"]["title"] == first.title
        assert row["provider_id"] == "paris"
        assert row["identity_id"] == "default"
        assert not service.storage.acquisitions()
    finally:
        service.close()


def test_sqlite_cross_sync_new_edition_same_active_loan_id_fails_closed(tmp_path):
    service = _service(tmp_path)
    try:
        loan = _loan("historic", credential="shared", account="member")
        library_mutation._persist_active_loans(
            service, "default", [loan], realize_expired=False,
        )
        before = _rights(service)
        changed = replace(loan, record_id="OTHER_EDITION", title="PRIVATE TITLE")
        with pytest.raises(ValueError, match="^loan_source_duplicate_conflict$"):
            library_mutation._persist_active_loans(
                service, "default", [changed], realize_expired=False,
            )
        assert _rights(service) == before
        assert len(service.storage.entitlements(state="active")) == 1
        # The same upstream loan cannot become active under another local
        # profile by changing the edition-dependent entitlement PK either.
        foreign = replace(changed, identity_id="alex")
        with pytest.raises(ValueError, match="^loan_source_duplicate_conflict$"):
            library_mutation._persist_active_loans(
                service, "alex", [foreign], realize_expired=False,
            )
        assert _rights(service) == before
    finally:
        service.close()


def test_different_borrowers_same_loan_id_are_independent(tmp_path):
    service = _service(tmp_path)
    try:
        member_a = _loan("common", credential="shared", account="reader-A")
        member_b = replace(member_a, source_account_id="reader-B",
                           record_id="other-edition")
        outcome = library_mutation._persist_active_loans(
            service, "default", [member_a, member_b], realize_expired=False,
        )
        assert outcome["persisted"] == 2
        assert {row["account_id"] for row in _rights(service)} == {
            "reader-A", "reader-B",
        }
    finally:
        service.close()


def test_storage_writer_refuses_ambiguous_direct_batch_and_keeps_atomicity(tmp_path):
    service = _service(tmp_path)
    try:
        source = SourceIdentity("paris", "cred", "reader", "default")
        a = Entitlement(source=source, item_id="book-1", entitlement_type="loan",
                        loan_id="loan-1", state="active")
        b = Entitlement(source=source, item_id="book-2", entitlement_type="loan",
                        loan_id="loan-1", state="active")
        with pytest.raises(ValueError, match="^loan_source_duplicate_conflict$"):
            service.storage.entitlement_put_positive_loans((a, b))
        assert _rights(service) == []
        assert service.storage.entitlement_put_positive_loans((a, a)) == (a.entitlement_id,)
        assert len(_rights(service)) == 1
    finally:
        service.close()


