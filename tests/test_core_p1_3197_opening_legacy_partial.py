"""#3197: opening sync never attests complete v1 loan inventories.

SQLite and synthetic registered provider sessions only; no network, ADEPT,
loan returns, acquisition or file removal.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.models import Loan
from tests.test_core_p0_1346_opening_source_isolation import _accounts, _state
from tests.test_sync_state_contract import _service


def _legacy_session(account, *, getter_calls):
    class LegacySession:
        provider_id = account.provider_id
        credential_id = account.credential_id
        connected = True
        capabilities = NS(loans=True, personal_library=False, digital_download=False)

        @property
        def last_loan_errors(self):
            getter_calls.append("read mutable upstream status")
            raise AssertionError("no side-channel consultation during opening")

        def close(self):
            pass

    return LegacySession()


def test_opening_legacy_empty_does_not_claim_complete_or_close_positive_right(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    getter_calls: list[str] = []
    try:
        paris, kobo, numilog = _accounts(service)
        service._sessions[
            service._session_key(paris.provider_id, paris.credential_id)
        ] = _legacy_session(paris, getter_calls=getter_calls)
        loan = Loan(
            id="loan-1", provider="DILICOM", medium="digital",
            record_id="edition-1", title="Synthetic",
            source_provider="paris", source_credential=paris.credential_id,
            source_account_id=paris.unique_id, identity_id="default",
            due_at="2099-01-01T00:00:00+00:00",
        )
        service._persist_active_loans("default", [loan])
        before = service.storage.entitlements(provider_id="paris")
        calls = []
        def read(account, *, force=False):
            assert force is True
            calls.append(account)
            return []
        monkeypatch.setattr(service, "_loans_for_account", read)
        service.mirror_personal_libraries = lambda: {}
        result = service.opening_sync()
        assert _state(service, paris)["status"] == "partial"
        assert _state(service, kobo)["status"] == "success"
        assert _state(service, numilog)["status"] == "success"
        assert len(calls) == 3 and calls == [paris, kobo, numilog]
        assert getter_calls == []
        assert service.storage.entitlements(provider_id="paris") == before
        default = next(row for row in result["identities"]
                       if row["identity_id"] == "default")
        assert default["status"] == "partial"
        assert default["provider_errors"] == [{"code": "loan_legacy_unverified"}]
    finally:
        service.close()


def test_no_loan_capability_stays_success_without_consulting_side_channel(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        paris, kobo, numilog = _accounts(service)
        service._sessions[
            service._session_key(paris.provider_id, paris.credential_id)
        ] = NS(
            provider_id=paris.provider_id, credential_id=paris.credential_id,
            connected=True, capabilities=NS(loans=False),
            last_loan_errors=[{"irrelevant": True}],
            close=lambda: None,
        )
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda _account, force=False: [])
        service.mirror_personal_libraries = lambda: {}
        service.opening_sync()
        assert _state(service, paris)["status"] == "success"
        assert _state(service, kobo)["status"] == "success"
        assert _state(service, numilog)["status"] == "success"
    finally:
        service.close()
