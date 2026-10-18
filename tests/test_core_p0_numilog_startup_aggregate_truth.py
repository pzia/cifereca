"""P0 #1905: aggregate startup outcomes must match exact durable receipts."""
from __future__ import annotations

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_startup_account_isolation import (
    _two_accounts, _safe_mock_downloads,
)
from tests.test_core_p0_1862_numilog_store_mirror import _row


def test_post_claim_binding_loss_is_blocked_not_a_false_complete(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: (_ for _ in ()).throw(
            AssertionError("stale account must not reach private library")
        )
        session_b.library = lambda: [_row(b.credential_id, 0)]
        downloaded = _safe_mock_downloads(monkeypatch, service)
        original = service.storage.numilog_materialization_claim_startup

        def after_claim(**kwargs):
            accepted = original(**kwargs)
            if accepted and kwargs["source_id"] == sid_a:
                session_a.connected = False
            return accepted

        monkeypatch.setattr(
            service.storage, "numilog_materialization_claim_startup",
            after_claim,
        )
        receipt = execute(broker, "default", "startup")
        assert receipt.ok is False
        assert receipt.facts["status"] == "partial"
        assert receipt.facts["blocked"] == 1
        assert receipt.facts["failed"] == 0
        assert receipt.facts["sources_claimed"] == 2
        assert receipt.facts["attempted"] == receipt.changed == 1
        assert [row.source_credential for row in downloaded] == [b.credential_id]
        assert broker.numilog_materialization_run_status(sid_a)["status"] == "blocked"
        assert broker.numilog_materialization_run_status(sid_b)["status"] == "complete"
        assert calls_a == calls_b == []
    finally:
        service.close()


def test_existing_claims_are_not_reinterpreted_as_completed_new_backups(
    tmp_path,
):
    from time import time
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: (_ for _ in ()).throw(
            AssertionError("throttled source a must not query Numilog")
        )
        session_b.library = lambda: (_ for _ in ()).throw(
            AssertionError("throttled source b must not query Numilog")
        )
        now = int(time())
        for sid in (sid_a, sid_b):
            assert service.storage.numilog_materialization_claim_startup(
                source_id=sid,
                now=now,
                expected_revision=service.storage.numilog_materialization_consent_revision(sid),
            )
        result = execute(broker, "default", "startup")
        assert result.ok is True and result.facts["status"] == "not_due"
        assert result.facts["sources_claimed"] == 0
        assert result.facts["blocked"] == result.facts["failed"] == 0
        assert result.changed == result.facts["attempted"] == 0
        assert broker.numilog_materialization_run_status(sid_a)["status"] == "running"
        assert broker.numilog_materialization_run_status(sid_b)["status"] == "running"
        assert calls_a == calls_b == []
    finally:
        service.close()
