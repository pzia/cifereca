"""#3171: no successful Numilog sync when owned purchases remain unresolved.

Uses synthetic source inventory and real SQLite receipts; zero downloads.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.numilog_materialization_startup import execute
from cifereca.plugin_api import SyncResult
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row


@pytest.mark.parametrize("reason", [
    "needs_reconciliation", "purchase_unproven", "source_not_ready",
])
def test_unresolved_owned_purchase_never_counts_as_complete(tmp_path, monkeypatch, reason):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        book = _row(account.credential_id, 0)
        session.library = lambda: [book]
        monkeypatch.setattr(
            SynchronizationBrokerImpl, "preflight_numilog_purchase",
            lambda *_a: SyncResult(ok=False, facts={"status": reason}),
        )
        result = execute(broker, account.identity_id, "startup")
        assert not result.ok
        recoverable = reason in {"needs_reconciliation", "purchase_unproven"}
        assert result.facts["status"] == ("failed" if recoverable else "blocked")
        assert result.facts["downloaded"] == 0
        assert result.facts["attempted"] == int(recoverable)
        assert result.facts["blocked"] == int(not recoverable)
        assert result.facts["failed"] == int(recoverable)
        assert result.facts["skipped"] == 0
        assert result.facts["source_results"][0]["status"] == result.facts["status"]
        durable = service.storage.numilog_materialization_run_status(sid)
        assert durable["status"] == result.facts["status"]
        assert durable["downloaded"] == 0
        # The fixture never implements delivery; reaching that boundary is
        # now correct for the single consented/recoverable owned purchase.
        assert calls == (["FORBIDDEN DOWNLOAD"] if recoverable else [])
        assert service.storage.acquisitions(provider_id="numilog") == []
    finally:
        service.close()


def test_owned_not_downloadable_is_pending_not_silently_ignored(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        book = replace(_row(account.credential_id, 0), downloadable=False)
        session.library = lambda: [book]
        # The old SQLite right can lack the live provider's downloadability
        # flag. Even a permissive preflight must not authorize delivery.
        monkeypatch.setattr(
            SynchronizationBrokerImpl, "preflight_numilog_purchase",
            lambda *_a: SyncResult(ok=True, facts={"status": "candidate_unverified"}),
        )
        monkeypatch.setattr(
            service, "acquire_direct_item",
            lambda *_a, **_kw: pytest.fail("non-downloadable owned item must not acquire"),
        )
        result = execute(broker, account.identity_id, "startup")
        assert not result.ok
        assert result.facts["status"] == "blocked"
        assert result.facts["blocked"] == 1
        assert result.facts["downloaded"] == result.facts["attempted"] == 0
        assert result.facts["skipped"] == 0
        assert service.storage.numilog_materialization_run_status(sid)["status"] == "blocked"
        assert calls == []
    finally:
        service.close()
