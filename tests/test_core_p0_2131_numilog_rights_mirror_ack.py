"""P0 #2131: a missing owned-rights mirror acknowledgement is a failed backup.

All source libraries are synthetic, with local SQLite startup receipts only.
No external Numilog GET, ADEPT, signed ACSM, or implicit recovery retry.
"""
from __future__ import annotations

import pytest

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_numilog_startup_account_isolation import (
    _two_accounts, _safe_mock_downloads,
)
from tests.test_core_p0_1862_numilog_store_mirror import _row


@pytest.mark.parametrize(("count", "ack"), [
    (1, 0), (1, None), (1, True), (1, -1), (2, 1),
])
def test_incomplete_or_untyped_rights_mirror_fails_before_acquisition(
    tmp_path, monkeypatch, count, ack,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: [_row(account.credential_id, i) for i in range(count)]
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            lambda *_args, **_kwargs: ack,
        )
        monkeypatch.setattr(
            service, "acquire_direct_item",
            lambda *_args, **_kwargs: pytest.fail(
                "Cannot download without a complete owned-rights mirror"
            ),
        )
        outcome = execute(broker, account.identity_id, "startup")
        assert outcome.ok is False
        assert outcome.facts["status"] == "failed"
        assert outcome.facts["attempted"] == outcome.facts["downloaded"] == 0
        assert outcome.facts["failed"] == 1
        state = broker.numilog_materialization_run_status(sid)
        assert state["status"] == "failed"
        assert state["attempted"] == state["downloaded"] == 0
        assert state["diagnostic"]["cause_phase"] == "rights_mirror"
        assert state["diagnostic"]["code"] == "rights_mirror_failed"
        assert service.storage.entitlements(provider_id="numilog") == []
        session.library = lambda: pytest.fail("No second private inventory this opening")
        assert execute(broker, account.identity_id, "startup").facts["status"] == "not_due"
        assert calls == []
    finally:
        service.close()


def test_empty_authenticated_library_can_still_finish_without_mirrored_rights(
    tmp_path, monkeypatch,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: []
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            lambda *_args, **_kwargs: 0,
        )
        result = execute(broker, account.identity_id, "startup")
        assert result.ok and result.facts["status"] == "complete"
        assert result.facts["downloaded"] == result.facts["attempted"] == 0
        assert broker.numilog_materialization_run_status(sid)["status"] == "complete"
        assert calls == []
    finally:
        service.close()


def test_failed_mirror_for_one_account_does_not_block_healthy_sibling(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    account_a, session_a, calls_a, sid_a = first
    account_b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: [_row(account_a.credential_id, 0)]
        session_b.library = lambda: [_row(account_b.credential_id, 0)]
        acquired = _safe_mock_downloads(monkeypatch, service)
        from cifereca.core.numilog_manifest_rights import mirror_numilog_manifest_rights
        def mirror(owner, rows, *, identity_id, credential_id, account_id):
            if credential_id == account_a.credential_id:
                return 0
            return mirror_numilog_manifest_rights(
                owner, rows, identity_id=identity_id,
                credential_id=credential_id, account_id=account_id,
            )
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            mirror,
        )
        result = execute(broker, "default", "startup")
        assert result.ok is False and result.facts["status"] == "partial"
        assert result.facts["attempted"] == result.facts["downloaded"] == 1
        assert result.facts["failed"] == 1
        assert [row.source_credential for row in acquired] == [account_b.credential_id]
        failed = broker.numilog_materialization_run_status(sid_a)
        succeeded = broker.numilog_materialization_run_status(sid_b)
        assert failed["status"] == "failed"
        assert failed["diagnostic"]["code"] == "rights_mirror_failed"
        assert succeeded["status"] == "complete" and succeeded["downloaded"] == 1
        assert calls_a == calls_b == []
    finally:
        service.close()
