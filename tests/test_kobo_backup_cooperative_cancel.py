"""Kobo P0 cooperative cancellation: durable, idempotent, non-destructive."""
from __future__ import annotations

import json
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


def test_cancel_between_books_persists_checkpoint_and_restart_resumes_only_missing(tmp_path, monkeypatch):
    service, _session = _service(tmp_path, _manifest())
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    first_calls = []
    monkeypatch.setattr(service, "_execute_kobo_acquisition", _fake_acquirer(first_calls))
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "historical",
        "status": "pending", "title": "Historical",
    })
    emitted = []
    try:
        first = service.sync_kobo_backup_account(
            "vault-1", progress=emitted.append,
            cancelled=lambda: bool(first_calls),
        )
        assert first_calls == ["item-1"]
        assert first["status"] == "cancelled"
        assert first["cancelled"] is True
        assert first["cancelled_sources"] == 1
        assert first["remaining"] == 2
        assert first["downloaded"] == 1
        assert first["attempted"] == 1
        assert first["legacy_pruned"] == 0
        assert first["sources"][0]["status"] == "cancelled"
        assert first["sources"][0]["remaining"] == 2
        assert any(event["event"] == "cancelled" and event["remaining"] == 2 for event in emitted)
        assert emitted[-1]["event"] == "done" and emitted[-1]["status"] == "cancelled"
        checkpoint = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")
        ))
        assert checkpoint["status"] == "cancelled"
        assert checkpoint["processed"] == 1
        assert checkpoint["total"] == 3
        assert checkpoint["counts"]["remaining"] == 2
        assert service._kobo_backup_account_due("vault-1") is False
        assert service.storage.backup_get("kobo", "vault-1", "historical") is not None
        saved_path = Path(service.storage.backup_get("kobo", "vault-1", "item-1")["local_path"])
        saved_bytes = saved_path.read_bytes()
    finally:
        service.close()

    # Fresh session and SQLite connection, no unverified index skip.
    restarted, _session = _service(tmp_path, _manifest())
    monkeypatch.setattr(restarted, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    resumed_calls = []
    monkeypatch.setattr(restarted, "_execute_kobo_acquisition", _fake_acquirer(resumed_calls))
    try:
        resumed = restarted.sync_kobo_backup_account("vault-1")
        assert resumed["status"] == "complete"
        assert restarted._kobo_backup_account_due("vault-1") is False
        assert resumed["unchanged"] == 1
        assert resumed["downloaded"] == 2
        assert resumed["attempted"] == 2
        assert resumed_calls == ["item-2", "item-3"]
        assert saved_path.read_bytes() == saved_bytes
        assert resumed["legacy_pruned"] == 1
        assert restarted.storage.backup_get("kobo", "vault-1", "historical") is None
        for i in range(1, 4):
            assert restarted.storage.backup_get("kobo", "vault-1", f"item-{i}")["status"] == "complete"
    finally:
        restarted.close()


def test_cancellation_before_manifest_fetch_does_not_access_provider(tmp_path, monkeypatch):
    service, session = _service(tmp_path, _manifest())
    monkeypatch.setattr(
        session, "backup_manifest",
        lambda: (_ for _ in ()).throw(AssertionError("must not fetch Kobo manifest")),
    )
    events = []
    try:
        report = service.sync_kobo_backup_account(
            "vault-1", cancelled=lambda: True, progress=events.append,
        )
        assert report["status"] == "cancelled"
        assert report["cancelled"] is True
        assert report["manifests_fetched"] == 0
        assert report["attempted"] == 0
        assert report["legacy_pruned"] == 0
        assert report["sources"][0]["status"] == "cancelled"
        state = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")
        ))
        assert state["processed"] == 0 and state["status"] == "cancelled"
        assert events[-1]["status"] == "cancelled"
    finally:
        service.close()


def test_cancellation_hook_exception_fails_closed_before_provider(tmp_path, monkeypatch):
    service, session = _service(tmp_path, _manifest())
    monkeypatch.setattr(
        session, "backup_manifest",
        lambda: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    try:
        def broken():
            raise RuntimeError("control unavailable")

        report = service.sync_kobo_backup_account("vault-1", cancelled=broken)
        assert report["status"] == "cancelled"
        assert report["attempted"] == 0
        assert report["manifests_fetched"] == 0
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
        assert service._kobo_backup_account_due("vault-1") is False
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


def test_pre_cancelled_resume_skips_catalog_adept_and_remnant_recovery(tmp_path, monkeypatch):
    """A cancelled operation must not perform slow prerequisites or reacquire."""
    from cifereca.core import kobo_backup

    service, session = _service(tmp_path, _manifest())
    calls = []
    monkeypatch.setattr(service, "catalog_scan",
                        lambda: calls.append("catalog") or (_ for _ in ()).throw(
                            AssertionError("cancelled operation scanned library")))
    monkeypatch.setattr(service, "kobo_adept_state",
                        lambda **_kw: calls.append("adept") or (_ for _ in ()).throw(
                            AssertionError("cancelled operation probed ADEPT")))
    monkeypatch.setattr(session, "backup_manifest",
                        lambda: (_ for _ in ()).throw(AssertionError("no manifest")))
    monkeypatch.setattr(kobo_backup, "_recover_local_remnants",
                        lambda *_a, **_kw: (_ for _ in ()).throw(
                            AssertionError("cancelled operation reconciled artifacts")))
    checks = []
    def one_shot_cancel():
        checks.append(True)
        return len(checks) == 1

    try:
        result = service.sync_kobo_backup_account(
            "vault-1", cancelled=one_shot_cancel,
        )
        assert result["status"] == "cancelled"
        assert result["manifests_fetched"] == 0
        assert result["attempted"] == 0
        assert result["downloaded"] == 0
        assert calls == [] and len(checks) == 1
        checkpoint = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")
        ))
        assert checkpoint["status"] == "cancelled"
        assert checkpoint["processed"] == 0
    finally:
        service.close()


def test_cancel_hook_failure_blocks_initial_io(tmp_path, monkeypatch):
    """A broken cancellation callback fails closed, before expensive work."""
    service, session = _service(tmp_path, _manifest())
    monkeypatch.setattr(service, "catalog_scan",
                        lambda: (_ for _ in ()).throw(AssertionError("catalog scanned")))
    monkeypatch.setattr(service, "kobo_adept_state",
                        lambda **_kw: (_ for _ in ()).throw(AssertionError("ADEPT probe")))
    monkeypatch.setattr(session, "backup_manifest",
                        lambda: (_ for _ in ()).throw(AssertionError("manifest requested")))
    try:
        def broken():
            raise RuntimeError("control detached")
        report = service.sync_kobo_backup_account("vault-1", cancelled=broken)
        assert report["status"] == "cancelled"
        assert report["manifests_fetched"] == 0
    finally:
        service.close()
