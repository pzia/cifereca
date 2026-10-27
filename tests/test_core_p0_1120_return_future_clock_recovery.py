"""P0 #1120: future-dated PNB claim cannot block inventory-only crash recovery.

No HTTP POST, Adobe mutation, filesystem removal or real provider traffic.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

import json
from datetime import datetime, timedelta, timezone

import pytest

from cifereca.core.loan_return_guard import return_key
from tests.test_core_0982_loan_return_reconciliation import _library, _loan, Session


@pytest.mark.parametrize(("offset", "should_check"), [
    (timedelta(days=365), True),
    (timedelta(minutes=4), True),
    (timedelta(seconds=45), False),
    (timedelta(seconds=-45), False),
    (timedelta(minutes=-4), True),
])
def test_inflight_clock_skew_preserves_post_guard_but_unblocks_inventory(
    tmp_path, offset, should_check,
):
    loan = _loan()
    session = Session([])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    service = _library(tmp_path, session=session)
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        assert service.storage.loan_return_claim(key, "op-future")[0]
        record = service.storage.loan_return_state(key)
        record["requested_at"] = (
            datetime.now(timezone.utc) + offset
        ).isoformat(timespec="seconds")
        service.storage.metadata_set(key, json.dumps(record))
        outcome = service.verify_return_digital(loan)
        assert outcome["status"] == (
            "provider_return_confirmed" if should_check else "request_sent"
        )
        assert session.checks == (["linked-b"] if should_check else [])
        assert session.posts == []
        # Repeated UI calls never reissue an uncertain return POST.
        assert service.return_digital(loan)["status"] == outcome["status"]
        assert session.posts == []
    finally:
        service.close()


@pytest.mark.parametrize("raw_time", [
    "9999-01-01T00:00:00+00:00",
    "2026-10-19T07:00:00",
    "https://private.invalid/loan?token=SECRET",
])
def test_untrusted_clock_never_reaches_provider_or_logs_as_private_text(
    tmp_path, raw_time, caplog,
):
    loan = _loan()
    session = Session([])
    service = _library(tmp_path, session=session)
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        assert service.storage.loan_return_claim(key, "op-invalid")[0]
        record = service.storage.loan_return_state(key)
        record["requested_at"] = raw_time
        service.storage.metadata_set(key, json.dumps(record))
        outcome = service.verify_return_digital(loan)
        assert outcome["status"] in {"request_sent", "provider_return_confirmed"}
        assert session.posts == []
        assert "SECRET" not in caplog.text
    finally:
        service.close()
