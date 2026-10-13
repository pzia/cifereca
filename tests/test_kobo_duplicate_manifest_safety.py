"""P0 Kobo: repeated product IDs are ambiguous, never acquisition authority.

These fixtures use real SQLite and deterministic local EPUBs, no network.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from tests.test_kobo_backup_reconcile_prune import (
    BackupSession, _manifest_row, _service, _write_epub,
)


def _backup(service, credential, identity, item_id, *, status="pending", path=None):
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "item_id": item_id,
        "revision_id": "rev-1", "title": item_id, "format": "EPUB",
        "status": status, "local_path": str(path) if path else None,
    })


def test_duplicate_product_versions_are_quarantined_before_any_acquisition(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[
        _manifest_row(item_id="dup", revision_id="rev-a"),
        _manifest_row(item_id="dup", revision_id="rev-b"),
        _manifest_row(item_id="known"),
    ])
    existing = service._kobo_backup_target_dir("default") / "known.epub"
    _write_epub(existing)
    _backup(service, "vault-1", "default", "known", status="complete", path=existing)
    _backup(service, "vault-1", "default", "historic")
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_args, **_kwargs: pytest.fail(
            "ambiguous Kobo revisions must never trigger acquisition"
        ),
    )
    try:
        before = existing.read_bytes()
        summary = service.sync_kobo_backup_account("vault-1")
        assert summary["status"] == "partial"
        assert summary["errors"] == 0
        assert summary["attempted"] == summary["downloaded"] == 0
        assert summary["unchanged"] == 1
        assert summary["malformed_manifest_items"] == 2
        assert summary["duplicate_manifest_items"] == 2
        assert summary["legacy_pruned"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "dup") is None
        assert service.storage.backup_get("kobo", "vault-1", "historic") is not None
        assert existing.read_bytes() == before
        checkpoint = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")
        ))
        assert checkpoint["status"] == "partial"
        assert checkpoint["processed"] == 3
        assert checkpoint["counts"]["duplicate_manifest_items"] == 2
        assert service.kobo_backup_diagnostic()["sources"][0]["duplicate_manifest_items"] == 2
    finally:
        service.close()


def test_duplicate_manifest_blocks_only_its_source_not_other_account_prune(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[
        _manifest_row(item_id="conflict", revision_id="old"),
        _manifest_row(item_id="conflict", revision_id="new"),
    ])
    first = service._sessions[service._session_key("kobo", "vault-1")]
    second_identity = replace(
        service.identities[0],
        identity_id="second", label="Second", directory_name="second",
    )
    service.identities.append(second_identity)
    service.storage.identity_put(second_identity)
    service.storage.provider_connection_put({
        "identity_id": "second", "provider_id": "kobo",
        "credential_id": "vault-2", "label": "Second",
    })
    second = BackupSession(
        descriptor=first.descriptor, credential_id="vault-2", label="Second",
        credentials={}, options={}, manifest=[_manifest_row(item_id="safe")],
    )
    second.connect()
    service._sessions[service._session_key("kobo", "vault-2")] = second

    local = service._kobo_backup_target_dir("second") / "safe.epub"
    _write_epub(local)
    _backup(service, "vault-2", "second", "safe", status="complete", path=local)
    _backup(service, "vault-1", "default", "historical-first")
    _backup(service, "vault-2", "second", "historical-second")
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_args, **_kwargs: pytest.fail("no content download required"),
    )
    try:
        result = service.sync_kobo_backup()
        assert result["status"] == "partial"
        assert result["duplicate_manifest_items"] == 2
        assert result["attempted"] == result["downloaded"] == 0
        assert result["legacy_pruned"] == 1
        assert [(row["credential_id"], row["status"]) for row in result["sources"]] == [
            ("vault-1", "partial"), ("vault-2", "complete"),
        ]
        assert result["sources"][0]["duplicate_manifest_items"] == 2
        assert result["sources"][1]["duplicate_manifest_items"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "historical-first") is not None
        assert service.storage.backup_get("kobo", "vault-2", "historical-second") is None
        assert local.is_file()
        # Repeating the malformed manifest remains a zero-transfer operation.
        again = service.sync_kobo_backup_account("vault-1")
        assert again["status"] == "partial"
        assert again["attempted"] == again["downloaded"] == 0
        assert again["legacy_pruned"] == 0
    finally:
        service.close()
