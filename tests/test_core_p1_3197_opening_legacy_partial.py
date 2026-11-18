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


@pytest.mark.parametrize("mode", ["legacy_opt_in", "capability_off", "canonical_off"])
def test_legacy_v1_only_dispatches_with_capability_and_without_canonical_policy(
    tmp_path, monkeypatch, mode,
):
    """Exercise real v1 adapter + opening dispatcher, not a provider download."""
    from cifereca.core.canonical_delivery_abi import has_complete_delivery_abi
    from cifereca.core.source_sync_consent import set_source_sync_consent
    from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
    from tests.test_core_p0_3171_atomic_publication_v14 import register

    service = _service(tmp_path)
    calls, getter_calls, dispatched = [], [], []
    try:
        paris, kobo, numilog = _accounts(service)
        register(service.storage, paris.identity_id, paris.provider_id,
                 paris.credential_id, account=paris.unique_id)
        row = Loan(
            id="old-loan", record_id="old-edition", provider="DILICOM",
            medium="digital", title="Synthetic v1",
            source_provider=paris.provider_id, source_credential=paris.credential_id,
            source_account_id=paris.unique_id, identity_id=paris.identity_id,
            ebook_url="https://example.invalid/synthetic.acsm",
            due_at="2099-01-01T00:00:00Z",
        )
        session = _legacy_session(
            paris, getter_calls=getter_calls, calls=calls, rows=[row],
        )
        session.capabilities = ProviderCapabilities(
            loans=True, digital_download=True,
            opening_loan_auto_acquisition=mode != "capability_off",
        )
        session.descriptor = ProviderDescriptor(
            id=paris.provider_id, display_name="Synthetic",
            description="Historical Plugin API 1.x fixture", state="ready",
            capabilities=session.capabilities,
        )
        assert not has_complete_delivery_abi(session)
        service._sessions[service._session_key(
            paris.provider_id, paris.credential_id,
        )] = session
        for account in (kobo, numilog):
            _register(service, account, getter_calls=getter_calls,
                      calls=calls, loan_capable=False)
        if mode == "canonical_off":
            policy = set_source_sync_consent(
                service, paris, enabled=False, expected_revision=None,
            )
            assert policy["configured"] and not policy["enabled"]

        def legacy_worker(loan, **kwargs):
            dispatched.append((loan.id, loan.record_id, loan.source_credential,
                               loan.source_account_id, kwargs))
            # This is the dispatch boundary; do not simulate a successful B.
            return {"status": "attention_required", "code": "synthetic_refusal"}

        service.mirror_personal_libraries = lambda: {}
        monkeypatch.setattr(service, "_acquire_active_loan_automatically", legacy_worker)
        result = service.opening_sync()
        default = next(item for item in result["identities"]
                       if item["identity_id"] == paris.identity_id)
        assert calls == [paris] and getter_calls == []
        assert _state(service, paris)["status"] == "partial"
        assert default["status"] == "partial"
        assert default["new_acquisitions"] == 0
        expected = [("old-loan", "old-edition", paris.credential_id,
                     paris.unique_id, {})] if mode == "legacy_opt_in" else []
        assert dispatched == expected
        assert service.storage.placements() == []
    finally:
        service.close()
