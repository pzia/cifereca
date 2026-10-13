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


def test_large_manifest_resumes_after_117_without_redownloading(tmp_path, monkeypatch):
    """#1348: a persisted partial batch resumes only the missing 405 items."""
    names = [f"book-{n:04d}" for n in range(522)]
    service, _session = _service(tmp_path, _manifest(*names))
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    downloaded = []
    interrupted = [False]

    def acquire(item, target_dir):
        if len(downloaded) == 117 and not interrupted[0]:
            interrupted[0] = True
            raise KeyboardInterrupt("simulated process interruption")
        downloaded.append(item.record_id)
        return _acquirer(item, target_dir)

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    key = _kobo_checkpoint_key("default", "vault-1")
    try:
        with pytest.raises(KeyboardInterrupt, match="interruption"):
            service.sync_kobo_backup_account("vault-1")
        assert len(downloaded) == 117
        checkpoint = json.loads(service.storage.metadata_get(key))
        assert checkpoint["processed"] == 117
        assert checkpoint["status"] == "interrupted"

        resumed = service.sync_kobo_backup_account("vault-1")
        assert resumed["status"] == "complete"
        assert resumed["downloaded"] == 405
        assert resumed["unchanged"] == 117
        assert resumed["attempted"] == 405
        assert len(downloaded) == 522
        assert len(set(downloaded)) == 522
        assert json.loads(service.storage.metadata_get(key))["status"] == "complete"
    finally:
        service.close()


def test_resume_after_closing_and_reopening_sqlite_service(tmp_path, monkeypatch):
    """#1348: persisted items survive a fresh Core instance, not only a retry."""
    names = [f"book-{n:04d}" for n in range(9)]
    downloaded = []
    interrupted = [False]

    def acquire(item, target_dir):
        if len(downloaded) == 4 and not interrupted[0]:
            interrupted[0] = True
            raise KeyboardInterrupt("restart simulation")
        downloaded.append(item.record_id)
        return _acquirer(item, target_dir)

    first, _ = _service(tmp_path, _manifest(*names))
    monkeypatch.setattr(first, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    monkeypatch.setattr(first, "_execute_kobo_acquisition", acquire)
    try:
        with pytest.raises(KeyboardInterrupt, match="restart simulation"):
            first.sync_kobo_backup_account("vault-1")
        assert len(downloaded) == 4
        checkpoint = json.loads(first.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")))
        assert checkpoint["processed"] == 4
        assert checkpoint["status"] == "interrupted"
    finally:
        first.close()

    second, _ = _service(tmp_path, _manifest(*names))
    monkeypatch.setattr(second, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    monkeypatch.setattr(second, "_execute_kobo_acquisition", acquire)
    try:
        result = second.sync_kobo_backup_account("vault-1")
        assert result["status"] == "complete"
        assert result["downloaded"] == 5
        assert result["unchanged"] == 4
        assert result["attempted"] == 5
        assert len(downloaded) == 9
        assert len(set(downloaded)) == 9
    finally:
        second.close()
