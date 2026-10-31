"""P0 #978: SQLite states are truthful across manual/opening sync and restart."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace as NS

from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.gui.sync_view import sync_source_presentation
from cifereca.models import Account, Loan
from cifereca.services import LibraryService
from cifereca.storage import Storage


def fake_broker(storage, *, fail=False, partial=False):
    account = Account(
        display_name="Compte Paris", unique_id="borrower-1",
        provider_id="paris", credential_id="paris-1", provider_label="Paris",
        identity_id="default",
    )
    session = NS(capabilities=NS(loans=True), connected=True)
    def synchronize(account, **kwargs):
        if fail:
            raise RuntimeError("source temporarily unavailable")
        if partial:
            return {"loans": 3, "attention_required": 1, "status": "partial"}
        return {"loans": 3}
    service = NS(
        storage=storage, accounts=[account],
        accounts_for_identity=lambda identity=None: [account],
        _session_key=lambda provider, credential: f"{provider}::{credential}",
        _sessions={"paris::paris-1": session},
        synchronize_account_source=synchronize,
    )
    return SynchronizationBrokerImpl(service)


def test_provider_exception_and_legacy_state_never_expose_private_text(tmp_path):
    """#1346: raw provider messages must not cross the public sync boundary."""
    storage = Storage(tmp_path / "redacted-sync.sqlite3")
    private = "https://private.invalid/book?token=SECRET_VALUE /home/reader/private.epub"
    try:
        broker = fake_broker(storage)
        source_id = broker.sources()[0].source_id
        key = "sync.source.state." + source_id
        storage.metadata_set(key, json.dumps({
            "status": "error", "last_success": "2026-10-01T07:00:00+00:00",
            "error": private,
        }))
        restored = broker.sources()[0]
        assert restored.error == "Échec de synchronisation"
        assert restored.last_success == "2026-10-01T07:00:00+00:00"
        assert private not in sync_source_presentation(restored).summary

        def fail(*_args, **_kwargs):
            raise RuntimeError(private)

        broker.service.synchronize_account_source = fail
        receipt = broker.synchronize_source(source_id)
        assert not receipt.ok and receipt.changed == 0
        assert receipt.facts == {"reason": "source_unavailable"}
        assert receipt.warnings == ("Échec de synchronisation",)
        assert private not in repr(receipt)
        assert private not in storage.metadata_get(key)
        assert broker.sources()[0].last_success == restored.last_success
    finally:
        storage.close()


def test_provider_result_projects_only_safe_counters_and_reason_codes(tmp_path):
    """#1346: never echo free-text errors, account IDs or provider extras."""
    storage = Storage(tmp_path / "private-sync-facts.sqlite3")
    secret = "reader-alice https://private.invalid/?bearer=SECRET_VALUE"
    try:
        broker = fake_broker(storage)
        source_id = broker.sources()[0].source_id
        broker.service.synchronize_account_source = lambda *_a, **_kw: {
            "loans": 3, "attention_required": 1, "status": "partial",
            "errors": [secret], "warnings": [secret],
            "provider": secret, "account_id": secret,
            "secret_total": 10000, "path": "/private/reader.epub",
        }
        receipt = broker.synchronize_source(source_id)
        assert not receipt.ok and receipt.changed == 4
        assert receipt.facts == {
            "loans": 3, "attention_required": 1,
            "status": "partial", "reason": "source_partial",
        }
        assert secret not in repr(receipt)
        assert broker.sources()[0].status == "partial"
        assert storage.metadata_get("sync.source.state." + source_id).find(secret) == -1
    finally:
        storage.close()


def test_source_sync_rejects_invalid_provider_reply_without_echoing_payload(tmp_path):
    storage = Storage(tmp_path / "invalid-sync-response.sqlite3")
    secret = "https://private.invalid/?token=SECRET_VALUE"
    try:
        broker = fake_broker(storage)
        source_id = broker.sources()[0].source_id
        broker.service.synchronize_account_source = lambda *_a, **_kw: secret
        receipt = broker.synchronize_source(source_id)
        assert not receipt.ok and receipt.facts == {"reason": "invalid_response"}
        assert receipt.changed == 0 and secret not in repr(receipt)

        broker.service.synchronize_account_source = lambda *_a, **_kw: {
            "loans": 1, "status": secret,
        }
        receipt = broker.synchronize_source(source_id)
        assert not receipt.ok and receipt.changed == 1
        assert receipt.facts == {"loans": 1, "reason": "invalid_response"}
        assert secret not in repr(receipt)
        assert broker.sources()[0].last_success == ""
    finally:
        storage.close()


def test_manual_success_sqlite_json_and_reopen(tmp_path):
    path = tmp_path / "state.sqlite3"
    storage = Storage(path)
    broker = fake_broker(storage)
    source = broker.sources()[0]
    try:
        assert source.connected and source.sync_capable
        result = broker.synchronize_source(source.source_id)
        assert result.ok and result.changed == 3
        raw = storage.metadata_get("sync.source.state." + source.source_id)
        saved = json.loads(raw)
        assert saved["status"] == "ok"
        assert saved["scope"] == "manual"
        assert saved["last_success"].endswith("+00:00")
    finally:
        storage.close()
    reopened = Storage(path)
    try:
        source = fake_broker(reopened).sources()[0]
        assert source.last_success
        assert source.status == "ok"
        assert "Dernier succès" in sync_source_presentation(source).summary
        assert "Jamais synchronisée" not in sync_source_presentation(source).summary
    finally:
        reopened.close()


def test_failure_and_partial_keep_prior_success(tmp_path):
    storage = Storage(tmp_path / "failure.sqlite3")
    try:
        healthy = fake_broker(storage)
        sid = healthy.sources()[0].source_id
        assert healthy.synchronize_source(sid).ok
        first = healthy.sources()[0].last_success
        failed = fake_broker(storage, fail=True)
        assert failed.synchronize_source(sid).ok is False
        state = failed.sources()[0]
        assert state.status == "error"
        assert state.last_success == first
        assert "Dernier succès confirmé" in sync_source_presentation(state).summary
        partial = fake_broker(storage, partial=True)
        outcome = partial.synchronize_source(sid)
        assert not outcome.ok and outcome.changed == 4
        current = partial.sources()[0]
        assert current.status == "partial"
        assert current.last_success == first
        assert "Partielle" in sync_source_presentation(current).sync_label
        assert "Jamais synchronisée" not in sync_source_presentation(current).summary
    finally:
        storage.close()


def test_legacy_repr_migrates_without_execution(tmp_path, caplog):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        broker = fake_broker(storage)
        key = "sync.source.state." + broker.sources()[0].source_id
        original = {"last_attempt": "2026-10-01T08:00:00+00:00",
                    "last_success": "2026-10-01T07:00:00+00:00",
                    "status": "ok", "error": ""}
        storage.metadata_set(key, original)  # historical str(dict)
        assert broker.sources()[0].last_success == original["last_success"]
        assert json.loads(storage.metadata_get(key)) == original
        storage.metadata_set(key, "__import__('os').system('echo BAD')")
        with caplog.at_level(logging.WARNING, logger="cifereca.sync"):
            assert broker.sources()[0].last_success == ""
        assert "invalid sync source metadata" in caplog.text
        assert storage.metadata_get(key).startswith("__import__")
    finally:
        storage.close()


def test_opening_sync_partial_never_invents_success(tmp_path, monkeypatch):
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "EBooks"))
    cfg.set("identities", [{
        "id": "default", "label": "Lectrice", "directory_name": "lectrice",
        "preferences": {"reading_languages": ["fr"]},
    }])
    cfg.set("providers.accounts", [])
    lib = LibraryService(cfg, Storage(tmp_path / "opening.sqlite3"), Diagnostics())
    try:
        account = Account(
            display_name="Paris", unique_id="borrower", provider_id="paris",
            credential_id="paris-1", provider_label="Paris", identity_id="default",
        )
        lib.accounts = [account]
        loan = Loan(
            id="loan-1", provider="PNB", medium="digital", record_id="r1",
            title="Livre", source_provider="paris", source_credential="paris-1",
            source_account_id="borrower", identity_id="default",
            ebook_url="https://example.invalid/fulfill.acsm",
            due_at="2099-01-01T00:00:00Z",
        )
        monkeypatch.setattr(lib, "_loans_for_account", lambda _a, force=False: [loan])
        monkeypatch.setattr(lib, "_acquire_active_loan_automatically",
                            lambda _loan: {"status": "attention_required", "loan_id": loan.id})
        monkeypatch.setattr(lib, "catalog_scan", lambda: {"scanned": 0})
        monkeypatch.setattr(lib, "kobo_backup_enabled", lambda: False)
        enable_synthetic_paris_opening(lib)
        result = lib.opening_sync()
        assert result["identities"][0]["status"] == "partial"
        key = "sync.source.state." + lib.sync_source_id(account)
        state = json.loads(lib.storage.metadata_get(key))
        assert state["status"] == "partial"
        assert state["scope"] == "opening"
        assert "last_success" not in state
        assert lib.storage.latest_sync_run("default")["status"] == "partial"
        # Reopening the same SQLite after the service closes must be stable.
    finally:
        lib.close()
    storage = Storage(tmp_path / "opening.sqlite3")
    try:
        assert json.loads(storage.metadata_get(key))["status"] == "partial"
    finally:
        storage.close()


def test_sync_source_declares_phase_capabilities_without_starting_acquisition(tmp_path):
    from cifereca.plugin_api import SyncSourceRef
    storage = Storage(tmp_path / "caps.sqlite")
    try:
        accounts = [
            Account(
                display_name="Kobo 1", unique_id="reader-1",
                provider_id="kobo", credential_id="vault-1",
                provider_label="Kobo", identity_id="alice",
            ),
            Account(
                display_name="Kobo 2", unique_id="reader-2",
                provider_id="kobo", credential_id="vault-2",
                provider_label="Kobo", identity_id="bob",
            ),
            Account(
                display_name="Paris", unique_id="paris-reader",
                provider_id="paris", credential_id="paris-1",
                provider_label="Paris", identity_id="alice",
            ),
            Account(
                display_name="Numilog", unique_id="store-reader",
                provider_id="numilog", credential_id="store-1",
                provider_label="Numilog", identity_id="alice",
            ),
        ]
        sessions = {
            "kobo::vault-1": NS(capabilities=NS(personal_library=True, backup=True),
                                connected=True),
            "kobo::vault-2": NS(capabilities=NS(personal_library=True, backup=False),
                                connected=True),
            "paris::paris-1": NS(capabilities=NS(loans=True), connected=True),
            "numilog::store-1": NS(capabilities=NS(personal_library=True), connected=True),
        }
        calls = []
        service = NS(
            storage=storage, accounts=accounts,
            accounts_for_identity=lambda identity=None: [
                row for row in accounts
                if identity is None or row.identity_id == identity
            ],
            _session_key=lambda provider, credential: f"{provider}::{credential}",
            _sessions=sessions,
            sync_kobo_backup=lambda *a, **k: calls.append(("acquire", a, k)),
            synchronize_account_source=lambda *a, **k: calls.append(("sync", a, k)),
        )
        broker = SynchronizationBrokerImpl(service)
        alice = broker.sources("alice")
        bob = broker.sources("bob")
        assert len(alice) == 3 and len(bob) == 1
        by_provider = {row.provider_id: row for row in alice}
        assert by_provider["kobo"].sync_capabilities == (
            "remote_inventory", "entitlements", "local_materialization", "scheduled_refresh",
        )
        assert by_provider["paris"].sync_capabilities == ("entitlements",)
        assert bob[0].sync_capabilities == ("remote_inventory", "entitlements", "scheduled_refresh")
        assert by_provider["numilog"].sync_capabilities == (
            "remote_inventory", "entitlements", "scheduled_refresh",
        )
        for source in (*alice, *bob):
            if source.provider_id in {"kobo", "numilog"}:
                assert broker.inventory_refresh_schedule(source.source_id) == {
                    "mode": "manual", "interval_minutes": 60,
                }
        assert SyncSourceRef(
            source_id="legacy", identity_id="alice",
            provider_id="kobo", account_id="legacy", label="Kobo",
        ).sync_capabilities == ()
        summary = sync_source_presentation(by_provider["kobo"]).summary
        assert "acquisition locale selon autorisation" in summary
        assert "Acquisition locale Kobo disponible sur commande dédiée" not in summary
        assert "acquisition locale selon autorisation" not in (
            sync_source_presentation(bob[0]).summary
        )
        assert calls == []  # Read-only projection never syncs or downloads.
    finally:
        storage.close()


def test_personal_inventory_alone_does_not_invent_a_scheduler_for_other_providers(tmp_path):
    storage = Storage(tmp_path / "unsupported-schedule.sqlite")
    try:
        broker = fake_broker(storage)
        broker.service._sessions["paris::paris-1"].capabilities.personal_library = True
        source = broker.sources()[0]
        assert "remote_inventory" in source.sync_capabilities
        assert "scheduled_refresh" not in source.sync_capabilities
    finally:
        storage.close()


def test_sync_source_capabilities_are_not_exposed_without_session_proof(tmp_path):
    storage = Storage(tmp_path / "no-session.sqlite")
    try:
        account = Account(
            display_name="Kobo sans session", unique_id="reader",
            provider_id="kobo", credential_id="missing",
            provider_label="Kobo", identity_id="alice",
        )
        service = NS(
            storage=storage, accounts=[account],
            accounts_for_identity=lambda identity=None: [account],
            _session_key=lambda provider, credential: f"{provider}::{credential}",
            _sessions={},
        )
        source = SynchronizationBrokerImpl(service).sources()[0]
        assert source.sync_capabilities == ()
        assert source.connected is False
        assert "acquisition locale selon autorisation" not in (
            sync_source_presentation(source).summary
        )
    finally:
        storage.close()
