"""P0 #2131: authenticated library must not admit other source/account rows.

All provider reads are replaced by synthetic results; these tests never issue
Numilog/Adobe HTTP or start a signed ACSM acquisition.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_numilog_startup_account_isolation import (
    _two_accounts, _safe_mock_downloads,
)
from tests.test_core_p0_1862_numilog_store_mirror import _row


PRIVATE = "https://private.invalid/item?auth=PRIVATE_NUMILOG_CREDENTIAL"


class HostileString:
    def __eq__(self, _other):
        raise AssertionError("Never compare a provider-supplied non-str value")

    def __str__(self):
        raise AssertionError("Never stringify provider-supplied private value")


@pytest.mark.parametrize("incorrect", [
    {"source_provider": "other"},
    {"source_credential": "foreign-credential"},
    {"source_collection": "catalog"},
    {"scenario": "NUMILOG_PUBLIC"},
    {"source_provider": HostileString()},
])
def test_foreign_manifest_row_is_diagnosed_before_rights_or_acquisition(
    tmp_path, monkeypatch, incorrect,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        good = _row(account.credential_id, 0)
        session.library = lambda: [good, replace(good, **incorrect)]
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            lambda *_a, **_kw: pytest.fail("foreign manifest must not persist rights"),
        )
        monkeypatch.setattr(
            service, "acquire_direct_item",
            lambda *_a, **_kw: pytest.fail("foreign manifest must not acquire"),
        )
        response = execute(broker, account.identity_id, "startup")
        assert response.ok is False
        assert response.facts["status"] == "failed"
        assert response.facts["attempted"] == response.facts["downloaded"] == 0
        assert response.facts["failed"] == 1
        status = broker.numilog_materialization_run_status(sid)
        assert status["status"] == "failed"
        assert status["attempted"] == status["downloaded"] == 0
        assert status["diagnostic"]["cause_phase"] == "manifest_fetch"
        assert status["diagnostic"]["code"] == "manifest_invalid"
        assert "PRIVATE_NUMILOG_CREDENTIAL" not in repr(status) + repr(response.facts)
        assert calls == []
        session.library = lambda: pytest.fail("no automatic retry after durable failure")
        assert execute(broker, account.identity_id, "startup").facts["status"] == "not_due"
    finally:
        service.close()


def test_wrong_bound_credential_does_not_block_separate_healthy_account(
    tmp_path, monkeypatch,
):
    service, broker, first, second = _two_accounts(tmp_path)
    account_a, session_a, calls_a, sid_a = first
    account_b, session_b, calls_b, sid_b = second
    try:
        # A private library response from account B cannot be attributed to
        # the independently consented account A, even with valid SearchResult.
        session_a.library = lambda: [_row(account_b.credential_id, 0)]
        session_b.library = lambda: [_row(account_b.credential_id, 0)]
        downloaded = _safe_mock_downloads(monkeypatch, service)
        response = execute(broker, "default", "startup")
        assert response.ok is False
        assert response.facts["status"] == "partial"
        assert response.facts["downloaded"] == response.facts["attempted"] == 1
        assert response.facts["failed"] == 1
        assert [row.source_credential for row in downloaded] == [account_b.credential_id]
        status_a = broker.numilog_materialization_run_status(sid_a)
        status_b = broker.numilog_materialization_run_status(sid_b)
        assert status_a["status"] == "failed"
        assert status_a["diagnostic"]["code"] == "manifest_invalid"
        assert status_a["attempted"] == 0
        assert status_b["status"] == "complete"
        assert status_b["downloaded"] == 1
        assert calls_a == calls_b == []
    finally:
        service.close()
