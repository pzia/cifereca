"""P0 Kobo: provider-discarded manifest rows must never authorize rights pruning.

All fixtures use real SQLite and fake connected provider sessions; no HTTP,
ADEPT or content download. The accepted subset remains available to inspect.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from tests.test_kobo_backup_reconcile_prune import _manifest_row, _service, _write_epub


def _restore_existing(service, *, item_id="historic", status="pending", path=None):
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": item_id, "revision_id": "rev-1",
        "title": item_id, "format": "EPUB", "status": status,
        "local_path": str(path) if path else None,
    })


def test_provider_rejection_blocks_prune_even_if_valid_subset_is_processed(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[_manifest_row()])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    local = service._kobo_backup_target_dir("default") / "book.epub"
    _write_epub(local)
    _restore_existing(service, item_id="book-1", status="complete", path=local)
    _restore_existing(service)
    failed_page = [True]

    def partially_valid_manifest():
        session.backup_manifest_rejection_count = 1 if failed_page[0] else 0
        return [_manifest_row()]

    monkeypatch.setattr(session, "backup_manifest", partially_valid_manifest)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("incomplete manifest may not download"),
    )
    try:
        first = service.sync_kobo_backup_account("vault-1")
        assert first["status"] == "partial"
        assert first["errors"] == 0
        assert first["downloaded"] == first["attempted"] == 0
        assert first["unchanged"] == 1
        assert first["malformed_manifest_items"] == 1
        assert first["provider_rejected_manifest_items"] == 1
        assert first["sources"][0]["provider_rejected_manifest_items"] == 1
        assert first["legacy_pruned"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "historic") is not None
        checkpoint = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")
        ))
        assert checkpoint["status"] == "partial"
        assert checkpoint["counts"]["provider_rejected_manifest_items"] == 1
        assert service.kobo_backup_diagnostic()["sources"][0][
            "provider_rejected_manifest_items"
        ] == 1

        failed_page[0] = False
        good = service.sync_kobo_backup_account("vault-1")
        assert good["status"] == "complete"
        assert good["provider_rejected_manifest_items"] == 0
        assert good["legacy_pruned"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "historic") is None
    finally:
        service.close()


def test_fully_rejected_empty_page_never_removes_owned_backup(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    _restore_existing(service)
    def invalid_empty_page():
        session.backup_manifest_rejection_count = 1
        return []
    monkeypatch.setattr(session, "backup_manifest", invalid_empty_page)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("rejected page cannot authorize acquisition"),
    )
    try:
        result = service.sync_kobo_backup_account("vault-1")
        assert result["manifest_items"] == 0
        assert result["manifests_fetched"] == 1
        assert result["status"] == "partial"
        assert result["provider_rejected_manifest_items"] == 1
        assert result["legacy_pruned"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "historic") is not None
    finally:
        service.close()


def test_refreshed_preview_marks_discarded_provider_rows_as_invalid(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    _restore_existing(service)
    def incomplete_manifest():
        session.backup_manifest_rejection_count = 2
        return [_manifest_row()]
    monkeypatch.setattr(session, "backup_manifest", incomplete_manifest)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("preview cannot transfer"),
    )
    before = service.storage.backup_list("kobo", "vault-1")
    try:
        preview = service.kobo_backup_preview(
            "vault-1", refresh_manifest=True,
        )
        assert preview["provider_rejected_manifest_items"] == 2
        assert preview["counts"]["invalid_manifest_items"] == 2
        assert preview["manifest_valid"] is False
        assert preview["blocking_items"] >= 2
        assert preview["acquisition_authorized"] is False
        assert service.storage.backup_list("kobo", "vault-1") == before
    finally:
        service.close()
