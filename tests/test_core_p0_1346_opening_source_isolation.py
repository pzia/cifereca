"""P0 #1346: opening source states are independent across accounts/identities."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account, Loan
from tests.test_sync_state_contract import _service


def _accounts(service):
    paris = Account(
        display_name="Paris", unique_id="borrower", provider_id="paris",
        credential_id="paris-private", identity_id="default",
    )
    kobo = Account(
        display_name="Kobo", unique_id="owner", provider_id="kobo",
        credential_id="kobo-private", identity_id="default",
    )
    numilog = Account(
        display_name="Numilog", unique_id="shopper", provider_id="numilog",
        credential_id="numilog-private", identity_id="alex",
    )
    service.accounts = [paris, kobo, numilog]
    return paris, kobo, numilog


def _state(service, account):
    source_id = SynchronizationBrokerImpl._source_id(account)
    raw = service.storage.metadata_get("sync.source.state." + source_id)
    return json.loads(raw)


def test_opening_provider_failure_does_not_poison_healthy_siblings_or_other_identity(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        paris, kobo, numilog = _accounts(service)
        private = "https://secret.invalid/fulfill.acsm?auth=SECRET_SIGNATURE"
        calls = []

        def loans(account, *, force=False):
            assert force is True
            calls.append(account.provider_id)
            if account is paris:
                raise RuntimeError(private)
            return []

        monkeypatch.setattr(service, "_loans_for_account", loans)
        enable_synthetic_paris_opening(service)
        result = service.opening_sync()
        by_identity = {
            row["identity_id"]: row for row in result["identities"]
        }
        assert by_identity["default"]["status"] == "partial"
        assert by_identity["alex"]["status"] == "success"
        assert _state(service, paris)["status"] == "failed"
        assert _state(service, kobo)["status"] == "success"
        assert _state(service, numilog)["status"] == "success"
        assert _state(service, paris).get("last_success", "") == ""
        assert _state(service, kobo)["last_success"]
        assert _state(service, numilog)["last_success"]
        assert calls == ["paris", "kobo", "numilog"]
        for account in (paris, kobo, numilog):
            assert "SECRET_SIGNATURE" not in json.dumps(_state(service, account))
            assert "paris-private" not in json.dumps(_state(service, account))
    finally:
        service.close()


def test_opening_partial_provider_rows_do_not_erase_other_source_success(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        paris, kobo, _numilog = _accounts(service)
        service._sessions[service._session_key("paris", paris.credential_id)] = NS(
            provider_id="paris", credential_id=paris.credential_id,
            connected=True, capabilities=NS(
                loans=True, personal_library=False, backup=False,
            ),
            # Legacy v1 inventory is partial even without an error side channel.
        )
        monkeypatch.setattr(service, "_loans_for_account", lambda _a, force=False: [])
        enable_synthetic_paris_opening(service)
        result = service.opening_sync()
        assert next(row for row in result["identities"]
                    if row["identity_id"] == "default")["status"] == "partial"
        assert _state(service, paris)["status"] == "partial"
        assert _state(service, paris).get("last_success", "") == ""
        assert _state(service, kobo)["status"] == "success"
        assert _state(service, kobo)["last_success"]
    finally:
        service.close()


def test_failed_loan_acquisition_marks_only_origin_source_partial(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        paris, kobo, _numilog = _accounts(service)
        loan = Loan(
            id="loan-a", provider="PNB", medium="digital",
            record_id="book-a", title="Fictional",
            source_provider="paris", source_credential=paris.credential_id,
            source_account_id=paris.unique_id, identity_id="default",
            ebook_url="https://example.invalid/fake.acsm",
            due_at="2099-01-01T00:00:00Z",
        )
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda account, force=False: [loan] if account is paris else [])
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            lambda _loan: {"status": "attention_required"})
        enable_synthetic_paris_opening(service)
        result = service.opening_sync()
        assert next(row for row in result["identities"]
                    if row["identity_id"] == "default")["attention_required"] == 1
        assert _state(service, paris)["status"] == "partial"
        assert _state(service, kobo)["status"] == "success"
    finally:
        service.close()


def test_opening_failure_preserves_previous_confirmed_source_success(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        paris, kobo, _numilog = _accounts(service)
        sid = service.sync_source_id(paris)
        previous = "2026-10-01T08:00:00+00:00"
        service.storage.metadata_set(
            "sync.source.state." + sid,
            json.dumps({"status": "ok", "last_success": previous}),
        )

        def loans(account, force=False):
            if account is paris:
                raise RuntimeError("temporary error")
            return []

        monkeypatch.setattr(service, "_loans_for_account", loans)
        enable_synthetic_paris_opening(service)
        service.opening_sync()
        assert _state(service, paris)["last_success"] == previous
        assert _state(service, paris)["status"] == "failed"
        assert _state(service, kobo)["status"] == "success"
    finally:
        service.close()
