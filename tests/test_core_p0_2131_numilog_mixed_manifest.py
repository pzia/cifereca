"""P0 #2131: mixed Numilog catalogue payloads must not look like a clean empty backup.

Authenticated provider library calls are replaced by synthetic data. All rights
writes, acquisition and ADEPT are guarded offline; only SQLite receipts run.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_numilog_startup_account_isolation import (
    _two_accounts, _safe_mock_downloads,
)
from tests.test_core_p0_1862_numilog_store_mirror import _row


PRIVATE = "https://private.invalid/LOAN/item?token=NEVER_ECHO_NUMILOG"


class HostileRow:
    def __str__(self):
        raise AssertionError("Never stringify malformed provider payload")

    def __repr__(self):
        raise AssertionError("Never repr malformed provider payload")


@pytest.mark.parametrize("broken_rows", [
    lambda valid: [valid, {"upstream_html": PRIVATE}],
    lambda valid: [HostileRow()],
    lambda valid: (valid, "malformed"),
])
def test_bad_member_refuses_manifest_before_rights_or_acquisition(
    tmp_path, monkeypatch, broken_rows,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: broken_rows(_row(account.credential_id, 0))
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            lambda *_args, **_kwargs: pytest.fail("malformed manifest cannot mirror rights"),
        )
        monkeypatch.setattr(
            service, "acquire_direct_item",
            lambda *_args, **_kwargs: pytest.fail("malformed manifest cannot acquire"),
        )
        result = execute(broker, account.identity_id, "startup")
        assert not result.ok
        assert result.facts["status"] == "failed"
        assert result.facts["attempted"] == 0
        assert result.facts["downloaded"] == 0
        assert result.facts["failed"] == 1
        state = broker.numilog_materialization_run_status(sid)
        assert state["status"] == "failed"
        assert state["attempted"] == state["downloaded"] == 0
        assert state["diagnostic"]["code"] == "manifest_invalid"
        assert state["diagnostic"]["cause_phase"] == "manifest_fetch"
        assert state["diagnostic"]["failed"] == 1
        assert PRIVATE not in json.dumps(state) + json.dumps(result.facts)
        # One failed durable claim must never cause an immediate blind retry.
        session.library = lambda: pytest.fail("No second GET on same opening")
        second = execute(broker, account.identity_id, "startup")
        assert second.ok and second.facts["status"] == "not_due"
        assert calls == []
    finally:
        service.close()


def test_malformed_first_account_never_blocks_healthy_consented_sibling(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        session_a.library = lambda: [_row(a.credential_id, 0), HostileRow()]
        session_b.library = lambda: [_row(b.credential_id, 0)]
        downloaded = _safe_mock_downloads(monkeypatch, service)
        result = execute(broker, "default", "startup")
        assert not result.ok
        assert result.facts["status"] == "partial"
        assert result.changed == 1
        assert result.facts["attempted"] == 1
        assert result.facts["failed"] == 1
        assert [row.source_credential for row in downloaded] == [b.credential_id]
        failed = broker.numilog_materialization_run_status(sid_a)
        good = broker.numilog_materialization_run_status(sid_b)
        assert failed["status"] == "failed"
        assert failed["diagnostic"]["code"] == "manifest_invalid"
        assert failed["attempted"] == 0
        assert good["status"] == "complete"
        assert good["attempted"] == good["downloaded"] == 1
        assert PRIVATE not in json.dumps(failed) + json.dumps(good)
        assert calls_a == calls_b == []
    finally:
        service.close()
