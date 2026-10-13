from __future__ import annotations

from pathlib import Path
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
        assert result["kobo"] == {
            "errors": 0,
            "status": "fresh_cache",
            "skipped_fresh": True,
        }
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
        assert result["kobo"] == {"status": "complete", "errors": 0}
        assert result["catalog"] == {"scanned": 4, "invalid": 0}
        assert calls == {"kobo": 1, "catalog": [True]}
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
        assert result["kobo"]["status"] == "fresh_cache_partial"
        assert result["kobo"]["last_batch_status"] == "attention_required"
        assert result["kobo"]["skipped_fresh"] is True
    finally:
        service.close()
