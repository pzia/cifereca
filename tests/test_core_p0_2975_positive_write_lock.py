"""P0 #2975: two SQLite writer connections may not mint one loan twice.

No BnF-Prêt plugin, provider I/O, ADEPT operation or file change.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from cifereca.acquisition_engine import Entitlement, SourceIdentity
from cifereca.storage import Storage


def _right(*, item="book", account="primary"):
    return Entitlement(
        source=SourceIdentity(
            provider_id="paris", credential_id="credential",
            account_id=account, identity_id="default",
        ),
        item_id=item, entitlement_type="loan", state="active",
        loan_id="one-supplier-loan",
    )


def test_two_independent_storage_writers_reject_conflicting_editions(tmp_path):
    filename = tmp_path / "shared.sqlite"
    first = Storage(filename)
    second = Storage(filename)
    gate = Barrier(2)
    try:
        def run(writer, item):
            gate.wait(timeout=5)
            try:
                return ("ok", writer.entitlement_put_positive_loans((_right(item=item),)))
            except ValueError as exc:
                return ("blocked", str(exc))
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(run, first, "edition-A")
            b = pool.submit(run, second, "edition-B")
            outcomes = [a.result(timeout=10), b.result(timeout=10)]
        assert sum(result[0] == "ok" for result in outcomes) == 1
        assert sum(result == ("blocked", "loan_duplicate_ambiguous")
                   for result in outcomes) == 1
        rights = first.entitlements(provider_id="paris")
        assert len(rights) == 1
        assert rights[0]["state"] == "active"
        assert rights[0]["item_id"] in {"edition-A", "edition-B"}
        assert not first.acquisitions()
        assert not first.placements()
    finally:
        first.close()
        second.close()


def test_same_upstream_loan_for_different_borrowers_is_not_a_collision(tmp_path):
    filename = tmp_path / "shared.sqlite"
    one = Storage(filename)
    two = Storage(filename)
    try:
        one.entitlement_put_positive_loans((_right(item="edition-A", account="alice"),))
        two.entitlement_put_positive_loans((_right(item="edition-B", account="bob"),))
        rights = one.entitlements(provider_id="paris")
        assert {r["account_id"] for r in rights} == {"alice", "bob"}
        assert {r["item_id"] for r in rights} == {"edition-A", "edition-B"}
    finally:
        one.close()
        two.close()


def test_second_writer_conflict_rolls_back_earlier_healthy_row_in_same_batch(tmp_path):
    filename = tmp_path / "shared.sqlite"
    one = Storage(filename)
    two = Storage(filename)
    try:
        one.entitlement_put_positive_loans((_right(item="first"),))
        previous = one.entitlements()
        unrelated = Entitlement(
            source=SourceIdentity("paris", "different-credential", "user", "default"),
            item_id="healthy", entitlement_type="loan", state="active",
            loan_id="healthy-loan",
        )
        with pytest.raises(ValueError, match="^loan_duplicate_ambiguous$"):
            two.entitlement_put_positive_loans((unrelated, _right(item="other")))
        assert one.entitlements() == previous
        assert not one.acquisitions()
    finally:
        one.close()
        two.close()
