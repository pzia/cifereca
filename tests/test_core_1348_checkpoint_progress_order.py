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


def test_archived_result_is_emitted_after_its_checkpoint(tmp_path, monkeypatch):
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
        if event["event"] == "skipped_archived":
            checkpoint = json.loads(service.storage.metadata_get(key))
            record = service.storage.backup_get("kobo", "vault-1", "archived")
            observed.append((checkpoint, record))

    try:
        result = service.sync_kobo_backup_account("vault-1", progress=progress)
        assert result["status"] == "complete"
        assert len(observed) == 1
        checkpoint, record = observed[0]
        assert checkpoint["status"] == "running"
        assert checkpoint["processed"] == 1
        assert checkpoint["counts"]["skipped_archived"] == 1
        assert record["status"] == "skipped_archived"
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


def test_resume_after_mid_manifest_checkpoint_failure_reuses_prior_files(tmp_path, monkeypatch):
    """#1348: after interruption at item 118/130, no completed EPUB is downloaded twice."""
    names = [f"book-{index:03d}" for index in range(130)]
    service, _session = _service(tmp_path, _manifest(*names))
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    attempts = []
    def acquire(item, target_dir):
        attempts.append(item.record_id)
        return _acquirer(item, target_dir)
    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    key = _kobo_checkpoint_key("default", "vault-1")
    original_set = service.storage.metadata_set
    failed_once = False
    def interrupt_at_118(name, value):
        nonlocal failed_once
        if name == key and not failed_once:
            checkpoint = json.loads(value)
            if checkpoint["status"] == "running" and checkpoint["processed"] == 118:
                failed_once = True
                raise OSError("simulated interruption after durable item write")
        return original_set(name, value)
    monkeypatch.setattr(service.storage, "metadata_set", interrupt_at_118)
    try:
        with pytest.raises(OSError, match="simulated interruption"):
            service.sync_kobo_backup_account("vault-1")
        assert len(attempts) == 118
        assert len(set(attempts)) == 118
        assert json.loads(service.storage.metadata_get(key))["processed"] == 117
        monkeypatch.setattr(service.storage, "metadata_set", original_set)
        resumed = service.sync_kobo_backup_account("vault-1")
        assert resumed["status"] == "complete"
        assert resumed["attempted"] == 12
        assert resumed["unchanged"] == 118
        assert len(attempts) == len(set(attempts)) == 130
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


def test_three_kobo_accounts_resume_only_interrupted_credential(tmp_path, monkeypatch):
    """#1348: 9/522/5 independent manifests, with interruption at item 117."""
    from tests.test_kobo_backup_fault_isolation import BackupSession

    service, first_session = _service(
        tmp_path, _manifest(*(f"a-{i:04d}" for i in range(9))))
    second_session = None
    third_session = None
    try:
        # One Kobo connection per identity is a production invariant.
        # Test checkpoint isolation across three identities, not an invalid
        # three-connections-on-one-identity configuration.
        service.config.set("identities", [
            {"id": "default", "label": "Default", "directory_name": "default"},
            {"id": "reader-2", "label": "Reader 2", "directory_name": "reader-2"},
            {"id": "reader-3", "label": "Reader 3", "directory_name": "reader-3"},
        ])
        service._sync_identity_configuration()
        for credential, identity, count, prefix in (
            ("vault-2", "reader-2", 522, "b"),
            ("vault-3", "reader-3", 5, "c"),
        ):
            service.storage.provider_connection_put({
                "identity_id": identity, "provider_id": "kobo",
                "credential_id": credential, "label": "Kobo",
            })
            session = BackupSession(
                descriptor=first_session.descriptor,
                credential_id=credential, label="Kobo", credentials={},
                options={}, manifest=_manifest(
                    *(f"{prefix}-{i:04d}" for i in range(count))),
            )
            session.connect()
            service._sessions[service._session_key("kobo", credential)] = session
            if credential == "vault-2":
                second_session = session
            else:
                third_session = session

        monkeypatch.setattr(service, "kobo_adept_state",
                            lambda **_kw: _ready_adept_state())
        downloads = []
        stopped = [False]

        def acquire(item, target_dir):
            if item.record_id.startswith("b-"):
                completed_b = sum(name.startswith("b-") for name in downloads)
                if completed_b == 117 and not stopped[0]:
                    stopped[0] = True
                    raise KeyboardInterrupt("second account interrupted")
            downloads.append(item.record_id)
            return _acquirer(item, target_dir)

        monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
        first = service.sync_kobo_backup_account("vault-1")
        assert first["downloaded"] == 9
        with pytest.raises(KeyboardInterrupt, match="second account"):
            service.sync_kobo_backup_account("vault-2")
        second_checkpoint = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("reader-2", "vault-2")))
        assert second_checkpoint["processed"] == 117
        assert second_checkpoint["total"] == 522
        assert second_checkpoint["status"] == "interrupted"
        third = service.sync_kobo_backup_account("vault-3")
        assert third["downloaded"] == 5

        resumed = service.sync_kobo_backup_account("vault-2")
        assert resumed["status"] == "complete"
        assert resumed["downloaded"] == 405
        assert resumed["unchanged"] == 117
        assert resumed["attempted"] == 405
        assert len(downloads) == 536
        assert len(set(downloads)) == 536
        for credential, identity, expected in (
            ("vault-1", "default", 9),
            ("vault-2", "reader-2", 522),
            ("vault-3", "reader-3", 5),
        ):
            checkpoint = json.loads(service.storage.metadata_get(
                _kobo_checkpoint_key(identity, credential)))
            assert checkpoint["status"] == "complete"
            assert checkpoint["total"] == expected
    finally:
        service.close()
