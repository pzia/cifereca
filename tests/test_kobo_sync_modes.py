from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import time

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from types import SimpleNamespace

import cifereca.services as services_module
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set(
        "identities",
        [{
            "id": "default",
            "label": "Hugues",
            "directory_name": "hugues",
            "preferences": {"reading_languages": ["fr"]},
        }],
    )
    config.set("providers.accounts", [])
    config.set("kobo_backup.interval_minutes", 60)

    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics())
    service.accounts = [
        Account(
            display_name="Kobo",
            unique_id="account",
            provider_id="kobo",
            credential_id="vault",
            provider_label="Kobo",
            identity_id="default",
        )
    ]
    service._sessions = {
        ("kobo", "vault"): SimpleNamespace(
            connected=True,
            provider_id="kobo",
            credential_id="vault",
            capabilities=SimpleNamespace(backup=True),
        )
    }
    return service


def _neutralize_unrelated_sync_work(service: LibraryService, monkeypatch) -> None:
    monkeypatch.setattr(service, "_kobo_adapter", lambda: SimpleNamespace(available=lambda: False))
    monkeypatch.setattr(service, "_loans_for_account", lambda account, force=False: [])
    monkeypatch.setattr(service, "sync_source_enabled", lambda account: True)
    monkeypatch.setattr(service, "mirror_personal_libraries", lambda: {"mirrored": 0})
    monkeypatch.setattr(service, "_reconcile_identity_adept_inventory", lambda identity_id: {"status": "skipped"})


def _enable_on_start(service: LibraryService, credential_id: str = "vault") -> None:
    """Model a real source binding and an explicit, separate startup opt-in."""
    from cifereca.brokers import SynchronizationBrokerImpl
    account = next(a for a in service.accounts if a.credential_id == credential_id)
    service.storage.provider_connection_put({
        "identity_id": account.identity_id, "provider_id": "kobo",
        "credential_id": credential_id, "enabled": True,
    })
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_local_materialization_schedule(
        broker._source_id(account), "on_start",
    ) == "on_start"


def test_fast_sync_uses_fresh_kobo_cache_and_skips_filesystem_scan(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        monkeypatch.setattr(services_module.time, "time", lambda: 100_000)
        service.storage.metadata_set("kobo_backup_last_success_at", 100_000)
        monkeypatch.setattr(service, "kobo_backup_enabled", lambda: True)
        monkeypatch.setattr(
            service,
            "_sync_kobo_backup_locked",
            lambda progress=None: (_ for _ in ()).throw(AssertionError("fresh Kobo cache must be reused")),
        )
        monkeypatch.setattr(
            service,
            "catalog_scan",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("fast sync must not scan the filesystem")),
        )

        result = service._opening_sync_locked(deep=False)

        assert result["sync_mode"] == "fast"
        assert result["catalog"]["filesystem_scan"] == "skipped"
        assert result["kobo"] == {"status": "manual_only", "skipped": True}
    finally:
        service.close()


def test_deep_sync_forces_kobo_backup_and_deep_catalog_scan(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        monkeypatch.setattr(services_module.time, "time", lambda: 100_000)
        service.storage.metadata_set("kobo_backup_last_success_at", 100_000)
        monkeypatch.setattr(service, "kobo_backup_enabled", lambda: True)

        calls = {"kobo": 0, "catalog": []}

        def sync_kobo(progress=None):
            calls["kobo"] += 1
            return {"status": "complete", "errors": 0}

        def scan_catalog(*, repair_misnamed=False):
            calls["catalog"].append(repair_misnamed)
            return {"scanned": 4, "invalid": 0}

        monkeypatch.setattr(service, "_sync_kobo_backup_locked", sync_kobo)
        monkeypatch.setattr(service, "catalog_scan", scan_catalog)

        result = service._opening_sync_locked(deep=True)

        assert result["sync_mode"] == "deep"
        # Deep scans the local catalog, never overrides manual acquisition.
        assert result["kobo"] == {"status": "manual_only", "skipped": True}
        assert result["catalog"] == {"scanned": 4, "invalid": 0}
        assert calls == {"kobo": 0, "catalog": [True]}
        assert int(service.storage.metadata_get("catalog_deep_last_at")) == 100_000
    finally:
        service.close()

def test_fast_sync_shows_throttled_incomplete_kobo_batch(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        monkeypatch.setattr(services_module.time, "time", lambda: 100_000)
        service.storage.metadata_set("kobo_backup_last_attempt_at", 100_000)
        service.storage.metadata_set("kobo_backup_last_batch_status", "attention_required")
        monkeypatch.setattr(service, "kobo_backup_enabled", lambda: True)
        monkeypatch.setattr(
            service, "_sync_kobo_backup_locked",
            lambda progress=None: (_ for _ in ()).throw(AssertionError("pending cache should throttle")),
        )
        result = service._opening_sync_locked(deep=False)
        # An incomplete legacy batch must not silently start a new download.
        assert result["kobo"] == {"status": "manual_only", "skipped": True}
    finally:
        service.close()


def test_opening_only_materializes_source_with_explicit_on_start_consent(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        second = replace(service.accounts[0], credential_id="vault-2",
                         unique_id="other-account")
        service.accounts.append(second)
        service._sessions[("kobo", "vault-2")] = SimpleNamespace(
            connected=True, provider_id="kobo", credential_id="vault-2",
            capabilities=SimpleNamespace(backup=True),
        )
        _enable_on_start(service, "vault")
        now = int(time.time())
        source1 = _kobo_checkpoint_key("default", "vault")
        source2 = _kobo_checkpoint_key("default", "vault-2")
        service.storage.metadata_set(source1, json.dumps({
            "schema_version": 1, "status": "complete",
            "total": 1, "processed": 1, "updated_at": now - 7200,
            "counts": {"unchanged": 1},
        }))
        service.storage.metadata_set(source2, json.dumps({
            "schema_version": 1, "status": "interrupted",
            "total": 1, "processed": 0, "updated_at": now,
            "counts": {},
        }))
        service.storage.metadata_set("kobo_backup_last_success_at", now)
        monkeypatch.setattr(service, "kobo_backup_enabled", lambda: True)
        calls = []
        monkeypatch.setattr(
            service, "_sync_kobo_backup_locked",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("global batch")),
        )

        def scoped(credential_id, progress=None):
            calls.append(credential_id)
            return {"status": "complete", "errors": 0, "unchanged": 1,
                    "sources": [{"source_id": source1.rsplit(".", 1)[-1],
                                 "status": "complete"}]}

        monkeypatch.setattr(service, "_sync_kobo_backup_account_locked", scoped)
        result = service._opening_sync_locked(deep=False)
        assert calls == ["vault"]
        assert result["kobo"]["scope"] == "accounts"
        assert result["kobo"]["synced_accounts"] == 1
        assert result["kobo"]["cached_accounts"] == 0
        assert result["kobo"]["status"] == "complete"
        assert len(result["kobo"]["sources"]) == 1
        assert result["kobo"]["sources"][0]["source_id"] == source1.rsplit(".", 1)[-1]
    finally:
        service.close()


def test_opening_all_cached_partial_sources_remain_visible(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        now = int(time.time())
        key = _kobo_checkpoint_key("default", "vault")
        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "status": "partial", "updated_at": now,
            "total": 3, "processed": 2, "counts": {"pending": 1},
        }))
        service.storage.metadata_set("kobo_backup_last_attempt_at", now)
        monkeypatch.setattr(service, "kobo_backup_enabled", lambda: True)
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("partial cached account must not be retried immediately")
            ),
        )
        result = service._opening_sync_locked(deep=False)
        assert result["kobo"]["status"] == "fresh_cache_partial"
        assert result["kobo"]["cached_accounts"] == 1
        assert result["kobo"]["synced_accounts"] == 0
        assert result["kobo"]["sources"][0]["status"] == "partial"
    finally:
        service.close()


def test_opening_rechecks_recently_interrupted_kobo_checkpoint_without_global_retry(tmp_path: Path, monkeypatch):
    """Recovery is scoped to the affected account, even with fresh global cache."""
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        now = int(time.time())
        key = _kobo_checkpoint_key("default", "vault")
        service.storage.metadata_set("kobo_backup_last_attempt_at", now)
        service.storage.metadata_set("kobo_backup_last_success_at", now)
        service.storage.metadata_set(
            key, json.dumps({
                "schema_version": 1, "status": "interrupted",
                "total": 3, "processed": 1, "updated_at": now,
                "counts": {"downloaded": 1},
            }),
        )
        monkeypatch.setattr(service, "kobo_backup_enabled", lambda: True)
        calls = []
        monkeypatch.setattr(
            service, "_sync_kobo_backup_locked",
            lambda progress=None: (_ for _ in ()).throw(
                AssertionError("no global acquisition on scoped recovery")
            ),
        )

        def scoped(credential_id, progress=None):
            calls.append(credential_id)
            return {
                "status": "complete", "errors": 0, "unchanged": 2,
                "sources": [{"source_id": key.rsplit(".", 1)[-1],
                             "status": "complete"}],
            }
        monkeypatch.setattr(service, "_sync_kobo_backup_account_locked", scoped)
        result = service._opening_sync_locked(deep=False)
        assert calls == ["vault"]
        assert result["kobo"]["scope"] == "accounts"
        assert result["kobo"]["synced_accounts"] == 1
        assert result["kobo"]["cached_accounts"] == 0
    finally:
        service.close()
