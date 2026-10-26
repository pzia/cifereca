"""P1 #3197: a v1 source read never attests complete loan coverage.

These tests use synthetic sessions and real SQLite only; no HTTP, DRM,
loan action, file mutation or provider login.
"""
from __future__ import annotations

import pytest

from tests.test_core_p0_2975_manual_source_loan_parity import (
    _fixture, _right,
)


@pytest.mark.parametrize("errors", [
    None, [], [{"upstream": "DILICOM", "code": "error"}],
    {"untrusted": "not a sequence"},
])
def test_legacy_source_is_partial_regardless_of_mutable_error_side_channel(
    tmp_path, monkeypatch, errors,
):
    service, account, _, _, inputs, session = _fixture(tmp_path, monkeypatch)
    try:
        session.last_loan_errors = errors
        result = service.synchronize_account_source(account)
        assert result["status"] == "partial"
        assert result["loans"] == 1
        assert _right(service, "primary", "edition")["state"] == "active"
        inputs[""] = []
        empty = service.synchronize_account_source(account)
        assert empty["status"] == "partial"
        assert empty["loans"] == 0
        assert _right(service, "primary", "edition")["state"] == "active"
        assert not service.storage.acquisitions()
    finally:
        service.close()


def test_legacy_source_never_evaluates_dynamic_side_channel_property(
    tmp_path, monkeypatch,
):
    service, account, _, _, _, session = _fixture(tmp_path, monkeypatch)
    calls = []

    class SideChannelPoison:
        provider_id = session.provider_id
        credential_id = session.credential_id
        connected = True
        capabilities = session.capabilities

        @property
        def last_loan_errors(self):
            raise AssertionError("mutable v1 error getter must never run")

        def loans(self, selected):
            calls.append(selected)
            return session.loans(selected)

        def close(self):
            pass

    key = service._session_key(account.provider_id, account.credential_id)
    service._sessions[key] = SideChannelPoison()
    try:
        result = service.synchronize_account_source(account)
        assert calls == [account]
        assert result["status"] == "partial" and result["loans"] == 1
        assert _right(service, "primary", "edition")["state"] == "active"
    finally:
        service.close()
