"""P0 #2131: an authenticated Numilog manifest must not hide malformed purchases.

Synthetic Numilog library only, with real local startup receipts/claims.
No network, Adobe runtime, original user documents, or automatic retry.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_numilog_startup_account_isolation import (
    _two_accounts, _safe_mock_downloads,
)
from tests.test_core_p0_1862_numilog_store_mirror import _row


PRIVATE = "https://private.invalid/download.acsm?auth=NEVER_EXPOSE"


class Hostile:
    def __str__(self):
        raise AssertionError("Never stringify private provider value")
    def __eq__(self, other):
        raise AssertionError("Never compare private provider value")
    def __hash__(self):
        raise AssertionError("Never hash private provider value")


@pytest.mark.parametrize("malformed", [
    lambda row: [row, row],
    lambda row: [replace(row, id="unscoped-foreign")],
    lambda row: [replace(row, record_id=PRIVATE)],
    lambda row: [replace(row, record_id=Hostile())],
    lambda row: [replace(row, owned="true")],
    lambda row: [replace(row, downloadable=Hostile())],
])
def test_bad_purchase_refuses_full_manifest_before_rights_or_acquisition(
    tmp_path, monkeypatch, malformed,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: malformed(_row(account.credential_id, 0))
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            lambda *_a, **_kw: pytest.fail("malformed purchases cannot write rights"),
        )
        monkeypatch.setattr(
            service, "acquire_direct_item",
            lambda *_a, **_kw: pytest.fail("malformed purchases cannot acquire"),
        )
        outcome = execute(broker, account.identity_id, "startup")
        assert outcome.ok is False
        assert outcome.facts["status"] == "failed"
        assert outcome.facts["downloaded"] == outcome.facts["attempted"] == 0
        assert outcome.facts["failed"] == 1
        receipt = broker.numilog_materialization_run_status(sid)
        assert receipt["status"] == "failed"
        assert receipt["attempted"] == receipt["downloaded"] == 0
        assert receipt["diagnostic"]["code"] == "manifest_invalid"
        assert receipt["diagnostic"]["cause_phase"] == "manifest_fetch"
        assert "NEVER_EXPOSE" not in json.dumps(receipt) + json.dumps(outcome.facts)
        session.library = lambda: pytest.fail("no automatic retry after failed claim")
        assert execute(broker, account.identity_id, "startup").facts["status"] == "not_due"
        assert calls == []
    finally:
        service.close()


def test_corrupt_purchase_for_first_account_does_not_starve_second(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        malformed = _row(a.credential_id, 0)
        session_a.library = lambda: [malformed, malformed]
        session_b.library = lambda: [_row(b.credential_id, 0)]
        acquired = _safe_mock_downloads(monkeypatch, service)
        outcome = execute(broker, "default", "startup")
        assert outcome.ok is False
        assert outcome.facts["status"] == "partial"
        assert outcome.facts["failed"] == 1
        assert outcome.facts["attempted"] == outcome.facts["downloaded"] == 1
        assert [x.source_credential for x in acquired] == [b.credential_id]
        failed = broker.numilog_materialization_run_status(sid_a)
        healthy = broker.numilog_materialization_run_status(sid_b)
        assert failed["status"] == "failed"
        assert failed["diagnostic"]["code"] == "manifest_invalid"
        assert failed["attempted"] == 0
        assert healthy["status"] == "complete"
        assert healthy["downloaded"] == 1
        assert calls_a == calls_b == []
    finally:
        service.close()
