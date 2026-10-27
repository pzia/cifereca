"""P0 #3197: typed/v1 loan readers keep raw causes, never false absence.

Offline sessions only. The inventory envelope is closed while the canonical
journal preserves the actual observed failure, exact immutable source scope,
and reader selected. No second read or rights mutation.
"""
from __future__ import annotations

import logging

import pytest

from cifereca.plugin_api import Cancelled, read_loan_inventory
from tests.test_core_p1_3119_loan_inventory_contract import account, session


SENTINEL = "SYNTHETIC_LOAN_INVENTORY_ORIGINAL_CAUSE"


@pytest.mark.parametrize("reader_kind", ("typed", "legacy"))
def test_failed_reader_journals_original_trace_and_never_retries(
    reader_kind, caplog,
):
    selected = account(
        provider="synthetic-loans", identity="reader-a",
        credential="card-a", borrower="sub-a",
    )

    def broken(_account):
        raise RuntimeError(SENTINEL)

    kwargs = {"hook": broken} if reader_kind == "typed" else {"reader": broken}
    source, calls = session(selected, **kwargs)
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        snapshot = read_loan_inventory(source, selected)
    assert calls == [(reader_kind, selected)]
    assert snapshot.status == "failed"
    assert snapshot.reason_code == "read_failed"
    assert snapshot.loans == () and snapshot.branches == ()
    assert snapshot.branch_claims_complete("dilicom", "digital") is False
    assert SENTINEL not in repr(snapshot)
    assert "TRACE loan inventory read failed" in caplog.text
    for detail in (
        "provider_id=synthetic-loans", "identity_id=reader-a",
        "credential_id=card-a", "account_id=sub-a",
        f"reader_kind={reader_kind}", "RuntimeError", SENTINEL,
    ):
        assert detail in caplog.text


def test_malformed_opted_in_snapshot_does_not_fall_back_to_legacy(caplog):
    selected = account()
    source, calls = session(selected, hook=lambda _: {"loans": []})
    source.loans = lambda _a: pytest.fail("second v1 loan fetch forbidden")
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        snapshot = read_loan_inventory(source, selected)
    assert calls == [("typed", selected)]
    assert snapshot.status == "failed" and snapshot.reason_code == "read_failed"
    assert snapshot.loans == ()
    assert "reader_kind=typed" in caplog.text
    assert "invalid_snapshot" in caplog.text


def test_cancelled_inventory_remains_cancelled_not_failed(caplog):
    selected = account()

    def stop(_):
        raise Cancelled("SYNTHETIC_USER_CANCEL")

    source, calls = session(selected, hook=stop)
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        snapshot = read_loan_inventory(source, selected)
    assert snapshot.status == "cancelled"
    assert snapshot.reason_code == "cancelled"
    assert calls == [("typed", selected)]
    assert "loan inventory read failed" not in caplog.text


def test_unavailable_session_never_logs_an_unobserved_reader_failure(caplog):
    selected = account()
    source, calls = session(selected, connected=False)
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        snapshot = read_loan_inventory(source, selected)
    assert snapshot.status == "failed"
    assert snapshot.reason_code == "unavailable"
    assert calls == []
    assert "loan inventory read failed" not in caplog.text
