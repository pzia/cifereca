"""P0 #1905: independent Numilog accounts have independent startup quotas."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.numilog_materialization_startup import execute
from cifereca.plugin_api import Cancelled, SyncResult
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_core_p0_numilog_auto_materialization import _authorize
from tests.test_sync_state_contract import _service


def _two_accounts(tmp_path):
    service = _service(tmp_path)
    a, session_a, calls_a = _connect(
        service, identity="default", credential="numilog-east", number=0,
    )
    b, session_b, calls_b = _connect(
        service, identity="default", credential="numilog-west", number=0,
    )
    broker = SynchronizationBrokerImpl(service)
    sid_a, sid_b = broker._source_id(a), broker._source_id(b)
    assert sid_a != sid_b
    _authorize(broker, sid_a)
    _authorize(broker, sid_b)
    return service, broker, (a, session_a, calls_a, sid_a), (
        b, session_b, calls_b, sid_b,
    )


def _safe_mock_downloads(monkeypatch, service):
    acquired = []
    def acquire(row, *, _admission=None):
        assert _admission() is row  # still admitted under the account gate
        acquired.append(row)
        return NS(metadata={"numilog_checkpoint_id": "FAKE_ONLY"})
    monkeypatch.setattr(service, "acquire_direct_item", acquire)
    monkeypatch.setattr(
        "cifereca.core.numilog_materialization_startup.index_delivered",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        SynchronizationBrokerImpl, "preflight_numilog_purchase",
        lambda *_a: SyncResult(ok=True, facts={"status": "candidate_unverified"}),
    )
    return acquired


def test_two_consenting_accounts_each_get_eight_downloads_not_one_shared_quota(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: [_row(a.credential_id, i) for i in range(11)]
        session_b.library = lambda: [_row(b.credential_id, i) for i in range(11)]
        acquired = _safe_mock_downloads(monkeypatch, service)
        result = execute(broker, "default", "startup")
        assert not result.ok and result.facts["status"] == "partial"
        # Both accounts have three purchases left after their independent
        # eight-attempt quotas; neither can be marked complete.
        assert result.facts["blocked"] == 2
        assert result.changed == result.facts["downloaded"] == 16
        assert result.facts["attempted"] == 16
        assert [r.source_credential for r in acquired] == (
            [a.credential_id] * 8 + [b.credential_id] * 8
        )
        for sid in (sid_a, sid_b):
            receipt = broker.numilog_materialization_run_status(sid)
            assert receipt["status"] == "partial"
            assert receipt["downloaded"] == receipt["attempted"] == 8
        assert calls_a == calls_b == []
    finally:
        service.close()


@pytest.mark.parametrize("failing_source", ["first", "second"])
def test_failed_account_does_not_starve_independently_opted_in_sibling(
    tmp_path, monkeypatch, failing_source,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        def fail():
            raise RuntimeError(
                "https://entrepot2r.numilog.com/private?token=NEVER_ECHO"
            )
        session_a.library = (
            fail if failing_source == "first"
            else lambda: [_row(a.credential_id, 0)]
        )
        session_b.library = (
            fail if failing_source == "second"
            else lambda: [_row(b.credential_id, 0)]
        )
        acquired = _safe_mock_downloads(monkeypatch, service)
        receipt = execute(broker, "default", "startup")
        assert not receipt.ok and receipt.facts["status"] == "partial"
        assert receipt.changed == receipt.facts["downloaded"] == 1
        assert receipt.facts["failed"] == 1
        assert len(acquired) == 1
        assert acquired[0].source_credential == (
            b.credential_id if failing_source == "first" else a.credential_id
        )
        own = [
            broker.numilog_materialization_run_status(sid)
            for sid in (sid_a, sid_b)
        ]
        assert [item["status"] for item in own] == (
            ["failed", "complete"] if failing_source == "first"
            else ["complete", "failed"]
        )
        assert [item["downloaded"] for item in own] == (
            [0, 1] if failing_source == "first" else [1, 0]
        )
        assert "NEVER_ECHO" not in repr(receipt)
        assert "NEVER_ECHO" not in repr(own)
        assert calls_a == calls_b == []
    finally:
        service.close()


def test_malformed_large_private_manifest_does_not_starve_second_account(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: [_row(a.credential_id, i) for i in range(2049)]
        session_b.library = lambda: [_row(b.credential_id, 0)]
        acquired = _safe_mock_downloads(monkeypatch, service)
        result = execute(broker, "default", "startup")
        assert not result.ok and result.changed == 1
        assert result.facts["failed"] == 1
        assert [r.source_credential for r in acquired] == [b.credential_id]
        assert broker.numilog_materialization_run_status(sid_a)["status"] == "failed"
        assert broker.numilog_materialization_run_status(sid_b)["status"] == "complete"
        assert calls_a == calls_b == []
    finally:
        service.close()


def test_user_cancellation_stops_all_accounts_immediately(tmp_path):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        def stop():
            raise Cancelled("stop")
        session_a.library = stop
        session_b.library = lambda: pytest.fail(
            "cancellation cannot turn into next source attempt"
        )
        with pytest.raises(Cancelled):
            execute(broker, "default", "startup")
        assert broker.numilog_materialization_run_status(sid_a)["status"] == "blocked"
        assert broker.numilog_materialization_run_status(sid_b)["status"] == "never"
        assert calls_a == calls_b == []
    finally:
        service.close()
