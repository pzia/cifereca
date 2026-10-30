"""P0 #3171: rights mirroring is advisory for a fresh downloadable offer.

A private authenticated provider delivery must not be blocked solely because
an optional local rights projection could not persist.
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
def test_incomplete_or_untyped_mirror_ack_does_not_veto_download(
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
        acquired = _safe_mock_downloads(monkeypatch, service)
        result = execute(broker, account.identity_id, "startup")
        assert result.ok and result.facts["status"] == "complete"
        assert result.facts["downloaded"] == result.facts["attempted"] == 1
        assert result.facts["failed"] == 0
        durable = broker.numilog_materialization_run_status(sid)
        assert durable["status"] == "complete"
        assert durable["attempted"] == durable["downloaded"] == 1
        assert len(acquired) == 1
        session.library = lambda: pytest.fail("throttled startup must not re-fetch")
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
        assert result.ok and result.facts["status"] == "complete"
        assert result.facts["attempted"] == result.facts["downloaded"] == 2
        assert result.facts["failed"] == 0
        assert [row.source_credential for row in acquired] == [
            a.credential_id, b.credential_id,
        ]
        left = broker.numilog_materialization_run_status(sid_a)
        right = broker.numilog_materialization_run_status(sid_b)
        assert left["status"] == "complete"
        assert left["downloaded"] == 1
        assert right["status"] == "complete"
        assert right["downloaded"] == 1
        assert calls_a == calls_b == []
    finally:
        service.close()
