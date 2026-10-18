"""#1905: an unfinalized SQLite receipt must never claim backup success."""
from __future__ import annotations

import pytest

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_startup_account_isolation import (
    _two_accounts, _safe_mock_downloads,
)
from tests.test_core_p0_1862_numilog_store_mirror import _row


@pytest.mark.parametrize("behavior", ["returned_false", "raised", "returned_one"])
def test_numilog_final_receipt_rejection_is_visible_without_private_error(
    tmp_path, monkeypatch, behavior,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        # Only one account has an eligible startup claim; no other account
        # should be marked complete because its run was throttled.
        service.storage.numilog_materialization_change_consent(
            sid_b, activate=False,
        )
        session_a.library = lambda: [_row(a.credential_id, 0)]
        acquired = _safe_mock_downloads(monkeypatch, service)
        original = service.storage.numilog_materialization_finish_startup
        def finalize(**kwargs):
            if behavior == "returned_false":
                return False
            if behavior == "returned_one":
                return 1  # truthy non-bool is not a durable receipt
            raise RuntimeError(
                "sqlite https://private.invalid/?auth=SIGNED_SECRET"
            )
        monkeypatch.setattr(
            service.storage, "numilog_materialization_finish_startup",
            finalize,
        )
        result = execute(broker, "default", "startup")
        assert result.ok is False
        assert result.facts["status"] == "partial"
        assert result.changed == result.facts["downloaded"] == 1
        assert result.facts["failed"] == 1
        assert result.facts["attempted"] == 1
        assert result.facts["sources_claimed"] == 1
        assert len(acquired) == 1
        assert "SIGNED_SECRET" not in repr(result)
        assert broker.numilog_materialization_run_status(sid_a)["status"] == "running"
        assert broker.numilog_materialization_run_status(sid_b)["status"] == "never"
        assert calls_a == calls_b == []
    finally:
        service.close()


@pytest.mark.parametrize("behavior", ["returned_false", "raised"])
def test_numilog_empty_backup_is_failed_when_complete_receipt_cannot_commit(
    tmp_path, monkeypatch, behavior,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        broker.revoke_numilog_materialization_consent(sid_b)
        session_a.library = lambda: []
        def refuse(**kwargs):
            if behavior == "returned_false":
                return False
            raise OSError("private SQLite path should never be shown")
        monkeypatch.setattr(
            service.storage, "numilog_materialization_finish_startup", refuse,
        )
        result = execute(broker, "default", "startup")
        assert result.ok is False
        assert result.facts["status"] == "failed"
        assert result.changed == result.facts["downloaded"] == 0
        assert result.facts["failed"] == 1
        assert result.facts["sources_claimed"] == 1
        assert "private SQLite" not in repr(result)
        assert broker.numilog_materialization_run_status(sid_a)["status"] == "running"
        assert calls_a == calls_b == []
    finally:
        service.close()


def test_numilog_receipt_failure_does_not_starve_next_consented_account(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: [_row(a.credential_id, 0)]
        session_b.library = lambda: [_row(b.credential_id, 0)]
        acquired = _safe_mock_downloads(monkeypatch, service)
        original = service.storage.numilog_materialization_finish_startup
        def selective(**kwargs):
            return (
                False if kwargs["source_id"] == sid_a
                else original(**kwargs)
            )
        monkeypatch.setattr(
            service.storage, "numilog_materialization_finish_startup",
            selective,
        )
        result = execute(broker, "default", "startup")
        assert result.ok is False
        assert result.changed == 2
        assert result.facts["status"] == "partial"
        assert result.facts["attempted"] == 2
        assert result.facts["failed"] == 1
        assert len(acquired) == 2
        assert broker.numilog_materialization_run_status(sid_a)["status"] == "running"
        assert broker.numilog_materialization_run_status(sid_b)["status"] == "complete"
        assert calls_a == calls_b == []
    finally:
        service.close()


def test_numilog_claim_refused_still_has_no_false_failure(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: pytest.fail("no library without claim")
        session_b.library = lambda: pytest.fail("no library without claim")
        monkeypatch.setattr(
            service.storage, "numilog_materialization_claim_startup",
            lambda **kwargs: False,
        )
        monkeypatch.setattr(
            service.storage, "numilog_materialization_finish_startup",
            lambda **kwargs: pytest.fail("no final receipt without claim"),
        )
        result = execute(broker, "default", "startup")
        assert result.ok is True and result.facts["status"] == "not_due"
        assert result.changed == 0
        assert result.facts["failed"] == result.facts["sources_claimed"] == 0
        assert calls_a == calls_b == []
    finally:
        service.close()
