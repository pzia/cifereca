"""Kobo P0: denied delegation is persisted per item, not retried en masse.

No provider traffic or actual acquisition is performed in these regression tests.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_backup_fault_isolation import (
    _ready_adept_state,
    _service,
    _write_epub,
)


def _manifest() -> list[dict[str, str]]:
    return [
        {"product_id": "blocked-1", "revision_id": "r1", "title": "Blocked One", "format": "EPUB"},
        {"product_id": "blocked-2", "revision_id": "r1", "title": "Blocked Two", "format": "EPUB"},
        {"product_id": "safe", "revision_id": "r1", "title": "Safe Book", "format": "EPUB"},
    ]


def test_scope_denial_does_not_retry_blocked_items_after_new_batch_or_restart(tmp_path: Path, monkeypatch):
    manifest = _manifest()
    service, _session = _service(tmp_path, manifest)
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    calls = []

    def first_acquire(item, target):
        calls.append(item.record_id)
        if item.record_id.startswith("blocked"):
            raise KoboVaultError(
                "Kobo-Vault: delegated scope refused", code="missing_delegation_scope",
                status_code=403,
            )
        artifact = Path(target) / "safe.epub"
        _write_epub(artifact)
        return SimpleNamespace(path=artifact, account_id="account", metadata={})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", first_acquire)
    try:
        first = service.sync_kobo_backup()
        assert first["attempted"] == 3
        assert first["downloaded"] == 1
        assert first["items_blocked_by_scope"] == 2
        assert calls == ["blocked-1", "blocked-2", "safe"]
        first_attempts = {
            record: service.storage.backup_get("kobo", "vault-1", record)["attempt_count"]
            for record in ("blocked-1", "blocked-2")
        }
        calls.clear()
        # A separate orphaned local file must not overwrite the durable
        # missing-scope decision or accidentally re-enable auto-retries.
        service.kobo_unlinked_local_count = lambda _identity: 2
        second = service.sync_kobo_backup()
        assert second["attempted"] == 0
        assert second["blocked_non_retryable"] == 2
        assert second["items_blocked_by_scope"] == 2
        assert second["attention_required"] == 2
        assert second["unchanged"] == 1
        assert calls == []
        for record, count in first_attempts.items():
            row = service.storage.backup_get("kobo", "vault-1", record)
            assert row["status"] == "attention_required"
            assert row["attention_code"] == "missing_delegation_scope"
            assert row["attempt_count"] == count
    finally:
        service.close()

    # Fresh service with a new provider session but same durable SQLite state.
    restarted, _session2 = _service(tmp_path, manifest)
    monkeypatch.setattr(restarted, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    monkeypatch.setattr(
        restarted, "_execute_kobo_acquisition",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("non-retryable scope must survive application restart")
        ),
    )
    try:
        resumed = restarted.sync_kobo_backup()
        assert resumed["attempted"] == 0
        assert resumed["blocked_non_retryable"] == 2
        assert resumed["items_blocked_by_scope"] == 2
        assert resumed["unchanged"] == 1
    finally:
        restarted.close()


def test_explicit_scope_retry_after_reauthorization_is_scoped_and_idempotent(tmp_path: Path, monkeypatch):
    service, _session = _service(tmp_path, _manifest())
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    calls = []

    def acquire(item, target):
        calls.append(item.record_id)
        if item.record_id.startswith("blocked") and not unlocked[0]:
            raise KoboVaultError(
                "delegation forbidden", code="missing_delegation_scope", status_code=403,
            )
        file = Path(target) / (item.record_id + ".epub")
        _write_epub(file)
        return SimpleNamespace(path=file, account_id="account", metadata={})

    unlocked = [False]
    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        first = service.sync_kobo_backup_account("vault-1")
        assert first["attempted"] == 3 and first["downloaded"] == 1
        assert service.sync_kobo_backup_account("vault-1")["attempted"] == 0
        unlocked[0] = True  # explicit provider reauthorization and reconnection
        calls.clear()
        resumed = service.sync_kobo_backup_account("vault-1", retry_scope_blocked=True)
        assert resumed["attempted"] == 2
        assert resumed["downloaded"] == 2
        assert resumed["unchanged"] == 1
        assert calls == ["blocked-1", "blocked-2"]
        assert service.sync_kobo_backup_account("vault-1")["attempted"] == 0
        for book in ("blocked-1", "blocked-2", "safe"):
            assert service.storage.backup_get("kobo", "vault-1", book)["status"] == "complete"
    finally:
        service.close()


def test_explicit_retry_never_bypasses_existing_local_artifact_guard(tmp_path: Path, monkeypatch):
    manifest = [
        {"product_id": "blocked-1", "revision_id": "r1", "title": "Blocked One", "format": "EPUB"},
    ]
    service, _session = _service(tmp_path, manifest)
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "blocked-1",
        "revision_id": "r1", "title": "Blocked One", "format": "EPUB",
        "status": "attention_required", "attention_code": "missing_delegation_scope",
    })
    candidate = service._kobo_backup_target_dir("default") / "Blocked One.epub"
    _write_epub(candidate)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("cannot redownload existing local candidate")
        ),
    )
    try:
        result = service.sync_kobo_backup_account("vault-1", retry_scope_blocked=True)
        assert result["attempted"] == 0
        assert result["reconcile_skipped"] == 1
        assert candidate.is_file()
        row = service.storage.backup_get("kobo", "vault-1", "blocked-1")
        assert row["status"] == "reconcile_required"
    finally:
        service.close()


def test_explicit_scope_retry_refuses_dangling_linked_symlink(tmp_path: Path, monkeypatch):
    service, _session = _service(tmp_path, [
        {"product_id": "blocked-1", "revision_id": "r2", "title": "Blocked",
         "format": "EPUB"},
    ])
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    # The unusual path is not generated from the Kobo title or product ID.
    linked = tmp_path / "external" / "old-name.epub"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(tmp_path / "missing" / "original.epub")
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "blocked-1",
        "revision_id": "r1", "title": "Old Title", "format": "EPUB",
        "status": "attention_required", "attention_code": "missing_delegation_scope",
        "local_path": str(linked),
    })
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("dangling linked Kobo artifact must not be redownloaded")
        ),
    )
    try:
        result = service.sync_kobo_backup_account("vault-1", retry_scope_blocked=True)
        assert result["attempted"] == 0
        assert result["reconcile_skipped"] >= 1
        row = service.storage.backup_get("kobo", "vault-1", "blocked-1")
        assert row["status"] == "reconcile_required"
        assert row["local_path"] == str(linked)
        assert linked.is_symlink()
    finally:
        service.close()


@pytest.mark.parametrize("prior_status,old_revision", [
    ("complete", "r1"),
    ("complete", "r0"),
    ("acquiring", "r1"),
    ("attention_required", "r1"),
])
def test_missing_previous_kobo_link_never_redownloads_or_erases_provenance(
    tmp_path: Path, monkeypatch, prior_status, old_revision,
):
    """A missing recorded file is not evidence of an unacquired book."""
    service, _session = _service(tmp_path, [
        {"product_id": "linked-1", "revision_id": "r1", "title": "Linked",
         "format": "EPUB"},
    ])
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    missing = tmp_path / "external" / "previous-copy.epub"
    assert not missing.exists()
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "linked-1",
        "revision_id": old_revision, "title": "Linked", "format": "EPUB",
        "status": prior_status, "local_path": str(missing),
    })
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("previously linked item must not redownload"),
    )
    try:
        for _ in range(2):
            summary = service.sync_kobo_backup_account(
                "vault-1", retry_scope_blocked=True,
            )
            assert summary["attempted"] == 0
            assert summary["downloaded"] == 0
            assert summary["reconcile_skipped"] == 1
            assert summary["status"] == "attention_required"
            row = service.storage.backup_get("kobo", "vault-1", "linked-1")
            assert row["status"] == "reconcile_required"
            assert row["local_path"] == str(missing)
            assert not missing.exists()
    finally:
        service.close()


@pytest.mark.parametrize("prior_status,revision", [
    ("complete", "r1"), ("complete", "old-revision"),
    ("ok", "r1"), ("ok", "old-revision"),
])
def test_complete_without_linked_path_never_redownloads(
    tmp_path: Path, monkeypatch, prior_status, revision,
):
    """#1344: completed ledger state with lost placement requires reconciliation."""
    service, _session = _service(tmp_path, [
        {"product_id": "once-downloaded", "revision_id": "r1",
         "title": "Already materialized", "format": "EPUB"},
    ])
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_kw: _ready_adept_state())
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "once-downloaded",
        "revision_id": revision, "status": prior_status,
        "title": "Already materialized", "format": "EPUB",
        "local_path": None,
        "sha256": "a" * 64, "size": 1234,
    })
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("completed provenance cannot silently redownload"),
    )
    try:
        for _ in range(2):
            result = service.sync_kobo_backup_account("vault-1")
            assert result["attempted"] == result["downloaded"] == 0
            assert result["reconcile_skipped"] == 1
            assert result["status"] == "attention_required"
            row = service.storage.backup_get(
                "kobo", "vault-1", "once-downloaded",
            )
            assert row["status"] == "reconcile_required"
            assert row["local_path"] is None
            assert row["sha256"] == "a" * 64
            assert row["size"] == 1234
    finally:
        service.close()
