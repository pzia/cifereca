"""P0 #2131: a private Numilog manifest is not backed up until rights were mirrored.

Network, ACSM and ADEPT operations are forbidden; only Core's actual startup
worker and SQLite diagnostic receipts are exercised.
"""
from __future__ import annotations

import pytest

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_numilog_startup_account_isolation import (
    _two_accounts, _safe_mock_downloads,
)
from tests.test_core_p0_1862_numilog_store_mirror import _row


@pytest.mark.parametrize("ack", [0, None, True, -1, 2, "1"])
def test_incomplete_or_untyped_mirror_ack_blocks_download_and_marks_failure(
    tmp_path, monkeypatch, ack,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: [_row(account.credential_id, 0)]
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            lambda *_args, **_kwargs: ack,
        )
        monkeypatch.setattr(
            service, "acquire_direct_item",
            lambda *_args, **_kwargs: pytest.fail(
                "never acquire a purchase not confirmed by the local rights mirror"
            ),
        )
        result = execute(broker, account.identity_id, "startup")
        assert not result.ok and result.facts["status"] == "failed"
        assert result.facts["downloaded"] == result.facts["attempted"] == 0
        assert result.facts["failed"] == 1
        durable = broker.numilog_materialization_run_status(sid)
        assert durable["status"] == "failed"
        assert durable["diagnostic"]["cause_phase"] == "rights_mirror"
        assert durable["diagnostic"]["code"] == "rights_mirror_failed"
        assert durable["attempted"] == 0 and durable["downloaded"] == 0
        session.library = lambda: pytest.fail("failed startup must not blindly re-fetch")
        assert execute(broker, account.identity_id, "startup").facts["status"] == "not_due"
        assert calls == []
    finally:
        service.close()


def test_empty_valid_manifest_needs_no_rights_to_finish(tmp_path, monkeypatch):
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
        assert result.facts["attempted"] == result.facts["downloaded"] == 0
        assert broker.numilog_materialization_run_status(sid)["status"] == "complete"
        assert calls == []
    finally:
        service.close()


def test_bad_mirror_of_first_account_does_not_starve_second_valid_account(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: [_row(a.credential_id, 0)]
        session_b.library = lambda: [_row(b.credential_id, 0)]
        from cifereca.core import numilog_materialization_startup as startup
        real_mirror = startup.mirror_numilog_manifest_rights
        def selective_mirror(owner, rows, **kwargs):
            if kwargs["credential_id"] == a.credential_id:
                return 0
            return real_mirror(owner, rows, **kwargs)
        monkeypatch.setattr(startup, "mirror_numilog_manifest_rights", selective_mirror)
        acquired = _safe_mock_downloads(monkeypatch, service)
        result = execute(broker, "default", "startup")
        assert not result.ok and result.facts["status"] == "partial"
        assert result.facts["attempted"] == result.facts["downloaded"] == 1
        assert result.facts["failed"] == 1
        assert [row.source_credential for row in acquired] == [b.credential_id]
        left = broker.numilog_materialization_run_status(sid_a)
        right = broker.numilog_materialization_run_status(sid_b)
        assert left["status"] == "failed"
        assert left["diagnostic"]["code"] == "rights_mirror_failed"
        assert right["status"] == "complete"
        assert right["downloaded"] == 1
        assert calls_a == calls_b == []
    finally:
        service.close()
