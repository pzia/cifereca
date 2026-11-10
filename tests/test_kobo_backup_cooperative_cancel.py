"""Kobo P0 cooperative cancellation: durable, idempotent, non-destructive."""
from __future__ import annotations

import json

import pytest
from pathlib import Path
from types import SimpleNamespace

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from tests.test_kobo_backup_fault_isolation import (
    _ready_adept_state,
    _service,
    _write_epub,
)


def _manifest():
    return [
        {"product_id": f"item-{i}", "revision_id": "r1",
         "title": f"Book {i}", "format": "EPUB"}
        for i in range(1, 4)
    ]


def _fake_acquirer(calls):
    def acquire(item, target_dir):
        calls.append(item.record_id)
        artifact = Path(target_dir) / f"{item.record_id}.epub"
        _write_epub(artifact)
        return SimpleNamespace(path=artifact, account_id="account", metadata={})
    return acquire


@pytest.mark.parametrize("cancel", ["true", "false", "exception", "delayed"])
def test_retired_w2_cancelled_or_not_never_starts_legacy_get(
    tmp_path, monkeypatch, cancel,
):
    from cifereca.providers import ProviderConfigurationError
    service, session = _service(tmp_path, _manifest())
    calls = []
    try:
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "default", "account_id": "account",
            "item_id": "historical", "status": "acquiring",
            "title": "Historical",
        })
        before = service.storage.backup_get("kobo", "vault-1", "historical")
        def forbidden(*_a, **_kw):
            pytest.fail("retired W2 callback or provider GET")
        monkeypatch.setattr(session, "backup_manifest", forbidden)
        monkeypatch.setattr(service, "_execute_kobo_acquisition", forbidden)
        monkeypatch.setattr(service.storage, "backup_put", forbidden)
        def cancelled():
            calls.append("cancel")
            if cancel == "exception":
                raise RuntimeError("detached cancellation")
            return cancel == "true"
        events = []
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_legacy_w2_retired_use_canonical_provider"):
            service.sync_kobo_backup_account(
                "vault-1", cancelled=cancelled,
                progress=events.append,
            )
        assert calls == [] and events == []
        assert service.storage.backup_get("kobo", "vault-1", "historical") == before
    finally:
        service.close()


def test_expired_cancelled_checkpoint_stays_manual_only(tmp_path):
    import time

    service, _session = _service(tmp_path, _manifest())
    try:
        now = int(time.time())
        service.storage.metadata_set(
            _kobo_checkpoint_key("default", "vault-1"),
            json.dumps({"schema_version": 1, "status": "cancelled",
                        "processed": 1, "total": 3, "updated_at": now - 86400}),
        )
        assert not hasattr(service, "_kobo_backup_account_due")
        # A crash must behave differently from a deliberate cancellation.
        service.storage.metadata_set(
            _kobo_checkpoint_key("default", "vault-1"),
            json.dumps({"schema_version": 1, "status": "interrupted",
                        "processed": 1, "total": 3, "updated_at": now}),
        )
        # #1365 supplies the interruption policy; the assertion here
        # prevents treating this diagnostic state as a user cancellation.
        row = service.kobo_backup_diagnostic()["sources"][0]
        assert row["status"] == "interrupted"
    finally:
        service.close()


