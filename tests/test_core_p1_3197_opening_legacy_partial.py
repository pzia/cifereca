"""#3197: opening Sync consumes actual installed v1 adapters, positive-only.

SQLite and synthetic registered sessions only. No network, ADEPT, loan
returns, acquisition or file removal. Do not mock the Core loan reader:
the fixture intentionally crosses the real ABI adapter.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Loan
from tests.test_core_p0_1346_opening_source_isolation import _accounts, _state
from tests.test_sync_state_contract import _service


def _legacy_session(account, *, getter_calls, calls, rows=(), loan_capable=True):
    class LegacySession:
        provider_id = account.provider_id
        credential_id = account.credential_id
        connected = True
        accounts = [account]
        capabilities = NS(
            loans=loan_capable, personal_library=False, digital_download=False,
            backup=False, opening_loan_auto_acquisition=False,
        )

        def loans(self, selected):
            assert loan_capable, "no read for a source without the loan capability"
            assert selected is account
            calls.append(selected)
            return list(rows)

        @property
        def last_loan_errors(self):
            getter_calls.append("read mutable upstream status")
            raise AssertionError("no side-channel consultation during opening")

        def close(self):
            pass

    return LegacySession()


def _register(service, account, **kwargs):
    session = _legacy_session(account, **kwargs)
    service._sessions[
        service._session_key(account.provider_id, account.credential_id)
    ] = session


@pytest.mark.parametrize("positive", [False, True], ids=["empty", "positive"])
def test_opening_legacy_does_not_claim_complete_or_close_positive_right(
    tmp_path, monkeypatch, positive,
):
    service = _service(tmp_path)
    getter_calls: list[str] = []
    calls = []
    try:
        paris, kobo, numilog = _accounts(service)
        loan = Loan(
            id="loan-1", provider="DILICOM", medium="digital",
            record_id="edition-1", title="Synthetic",
            source_provider="paris", source_credential=paris.credential_id,
            source_account_id=paris.unique_id, identity_id="default",
            due_at="2099-01-01T00:00:00+00:00",
        )
        service._persist_active_loans("default", [loan])
        before = service.storage.entitlements(provider_id="paris")
        new_loan = replace(loan, id="loan-2", record_id="edition-2")
        _register(service, paris, getter_calls=getter_calls, calls=calls,
                  rows=[new_loan] if positive else [])
        for account in (kobo, numilog):
            _register(service, account, getter_calls=getter_calls,
                      calls=calls, loan_capable=False)
        service.mirror_personal_libraries = lambda: {}
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda *_a, **_kw: pytest.fail("no Core v1 reader"))
        result = service.opening_sync()
        assert _state(service, paris)["status"] == "partial"
        assert _state(service, kobo)["status"] == "success"
        assert _state(service, numilog)["status"] == "success"
        assert calls == [paris]
        assert getter_calls == []
        after = service.storage.entitlements(provider_id="paris")
        if positive:
            assert {row["loan_id"]: row["state"] for row in after} == {
                "loan-1": "active", "loan-2": "active",
            }
        else:
            assert after == before
        default = next(row for row in result["identities"]
                       if row["identity_id"] == "default")
        assert default["status"] == "partial"
        assert default["provider_errors"] == [{"code": "loan_legacy_unverified"}]
    finally:
        service.close()


def test_no_loan_capability_stays_success_without_consulting_side_channel(tmp_path, monkeypatch):
    service = _service(tmp_path)
    calls = []
    getter_calls = []
    try:
        paris, kobo, numilog = _accounts(service)
        for account in (paris, kobo, numilog):
            _register(service, account, getter_calls=getter_calls,
                      calls=calls, loan_capable=False)
        service.mirror_personal_libraries = lambda: {}
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda *_a, **_kw: pytest.fail("no Core v1 reader"))
        service.opening_sync()
        assert _state(service, paris)["status"] == "success"
        assert _state(service, kobo)["status"] == "success"
        assert _state(service, numilog)["status"] == "success"
        assert calls == getter_calls == []
    finally:
        service.close()
