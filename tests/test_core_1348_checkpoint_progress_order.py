"""#1348: item progress is observable only after its durable Kobo checkpoint.

Provider and acquisition calls are synthetic; SQLite is a real temporary file.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_backup_fault_isolation import (
    _ready_adept_state,
    _service,
    _write_epub,
)


def _manifest(*names):
    return [
        {"product_id": name, "revision_id": "r1", "title": name, "format": "EPUB"}
        for name in names
    ]


def _acquirer(item, target_dir):
    if item.record_id == "bad":
        raise KoboVaultError("temporary failure", code="temporary", status_code=503)
    target = Path(target_dir) / f"{item.record_id}.epub"
    _write_epub(target)
    return SimpleNamespace(path=target, account_id="account", metadata={})


def test_item_outcomes_observe_committed_checkpoint_not_previous_item(tmp_path, monkeypatch):
    service, _session = _service(tmp_path, _manifest("good", "bad", "last"))
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    monkeypatch.setattr(service, "_execute_kobo_acquisition", _acquirer)
    key = _kobo_checkpoint_key("default", "vault-1")
    observed = []

    def progress(event):
        if event["event"] not in {"downloaded", "item_error"}:
            return
        checkpoint = json.loads(service.storage.metadata_get(key))
        record = service.storage.backup_get("kobo", "vault-1", event["item_id"])
        observed.append((event["event"], checkpoint, record))
        # Assertions are outside the progress callback: Core deliberately
        # swallows callback exceptions so UI feedback cannot abort a batch.

    try:
        summary = service.sync_kobo_backup_account("vault-1", progress=progress)
        assert summary["status"] == "partial"
        assert summary["attempted"] == 3
        assert summary["downloaded"] == 2
        assert summary["errors"] == 1
        assert [(name, cp["processed"]) for name, cp, _ in observed] == [
            ("downloaded", 1), ("item_error", 2), ("downloaded", 3),
        ]
        for name, checkpoint, record in observed:
            assert checkpoint["status"] == "running"
            assert checkpoint["last_item_digest"] is not None
            assert record["status"] == (
                "complete" if name == "downloaded" else "error"
            )
        assert [cp["counts"]["downloaded"] for _, cp, _ in observed] == [1, 1, 2]
        assert [cp["counts"]["errors"] for _, cp, _ in observed] == [0, 1, 1]
        assert json.loads(service.storage.metadata_get(key))["status"] == "partial"
    finally:
        service.close()


def test_attention_result_is_emitted_after_its_checkpoint(tmp_path, monkeypatch):
    rows = [dict(_manifest("archived")[0], archived=True)]
    service, _session = _service(tmp_path, rows)
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("archived item cannot be acquired"),
    )
    key = _kobo_checkpoint_key("default", "vault-1")
    observed = []

    def progress(event):
        if event["event"] == "attention_required":
            checkpoint = json.loads(service.storage.metadata_get(key))
            record = service.storage.backup_get("kobo", "vault-1", "archived")
            observed.append((checkpoint, record))

    try:
        result = service.sync_kobo_backup_account("vault-1", progress=progress)
        assert result["status"] == "attention_required"
        assert len(observed) == 1
        checkpoint, record = observed[0]
        assert checkpoint["status"] == "running"
        assert checkpoint["processed"] == 1
        assert checkpoint["counts"]["attention_required"] == 1
        assert record["status"] == "attention_required"
    finally:
        service.close()


def test_checkpoint_write_failure_cannot_advertise_completed_item(tmp_path, monkeypatch):
    service, _session = _service(tmp_path, _manifest("good"))
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    calls = []
    monkeypatch.setattr(service, "_execute_kobo_acquisition",
                        lambda item, target_dir: (calls.append(item.record_id) or
                            _acquirer(item, target_dir)))
    key = _kobo_checkpoint_key("default", "vault-1")
    real_set = service.storage.metadata_set
    announced = []

    def fail_final_item_checkpoint(name, value):
        if name == key:
            row = json.loads(value)
            if row["status"] == "running" and row["processed"] == 1:
                raise OSError("SQLite unavailable during item checkpoint")
        return real_set(name, value)

    monkeypatch.setattr(service.storage, "metadata_set", fail_final_item_checkpoint)
    try:
        with pytest.raises(OSError, match="checkpoint"):
            service.sync_kobo_backup_account("vault-1", progress=announced.append)
        assert not any(e["event"] == "downloaded" for e in announced)
        assert service.storage.backup_get("kobo", "vault-1", "good")["status"] == "complete"
        checkpoint = json.loads(service.storage.metadata_get(key))
        assert checkpoint["processed"] == 0

        # The item's durable row is still safe to reuse after recovery.
        monkeypatch.setattr(service.storage, "metadata_set", real_set)
        resumed = service.sync_kobo_backup_account("vault-1")
        assert resumed["status"] == "complete"
        assert resumed["attempted"] == 0
        assert resumed["unchanged"] == 1
        assert calls == ["good"]
    finally:
        service.close()
