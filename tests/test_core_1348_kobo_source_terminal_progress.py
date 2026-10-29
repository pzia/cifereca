"""#1348 — Kobo source terminal progress must match its durable checkpoint."""
from __future__ import annotations

import json

from tests.test_kobo_backup_fault_isolation import _service, _ready_adept_state
from cifereca.core.kobo_backup import _kobo_checkpoint_key


def test_source_done_is_emitted_after_terminal_checkpoint(tmp_path, monkeypatch):
    service, _session = _service(tmp_path, [{
        "product_id": "archived", "revision_id": "r1",
        "title": "Archived", "archived": True, "format": "EPUB",
    }])
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    seen = []

    def progress(event):
        if event["event"] == "source_done":
            checkpoint = json.loads(service.storage.metadata_get(
                _kobo_checkpoint_key("default", "vault-1"),
            ))
            seen.append((dict(event), checkpoint))

    try:
        result = service.sync_kobo_backup_account("vault-1", progress=progress)
        assert result["status"] == "complete"
        assert len(seen) == 1
        event, checkpoint = seen[0]
        assert event["status"] == checkpoint["status"] == "complete"
        assert event["manifest_items"] == checkpoint["total"] == 1
        assert checkpoint["processed"] == 1
        assert event["skipped_archived"] == checkpoint["counts"]["skipped_archived"] == 1
        assert event["remaining"] == checkpoint["counts"]["remaining"] == 0
    finally:
        service.close()


def test_failed_manifest_has_terminal_checkpoint_without_false_success(tmp_path, monkeypatch):
    service, session = _service(tmp_path, [])
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())

    def fail_manifest():
        raise RuntimeError("synthetic provider outage")

    session.backup_manifest = fail_manifest
    events = []
    try:
        result = service.sync_kobo_backup_account("vault-1", progress=events.append)
        assert result["status"] in {"failed", "aborted"}
        terminal = [event for event in events if event["event"] == "source_done"]
        assert len(terminal) == 1
        checkpoint = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1"),
        ))
        assert checkpoint["status"] in {"failed", "aborted"}
        assert terminal[0]["status"] == checkpoint["status"]
        assert terminal[0]["manifest_items"] == checkpoint["total"] == 0
        assert terminal[0]["errors"] == checkpoint["counts"]["errors"] == 1
        assert terminal[0]["remaining"] == checkpoint["counts"]["remaining"] == 0
    finally:
        service.close()
