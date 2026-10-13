"""Kobo P0: raw manifest rights flags must be real JSON booleans.

A malformed/denied ownership flag cannot authorize a download, create a
backup row, silently mark the source complete or prune previous rights.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.kobo_backup import (
    _kobo_checkpoint_key, _kobo_manifest_flags_invalid,
)
from tests.test_kobo_backup_reconcile_prune import _manifest_row, _service, _write_epub


@pytest.mark.parametrize("override", [
    {"owned": False},
    {"owned": "false"},
    {"owned": "true"},
    {"owned": 0},
    {"owned": 1},
    {"downloadable": "false"},
    {"downloadable": "true"},
    {"downloadable": 0},
    {"downloadable": 1},
    {"archived": "false"},
    {"archived": "true"},
    {"archived": 0},
])
def test_ambiguous_flags_never_trigger_acquisition_or_prune(
    tmp_path, monkeypatch, override,
):
    raw = {**_manifest_row(), **override}
    service = _service(tmp_path, manifest=[raw])
    saved = service._kobo_backup_target_dir("default") / "old.epub"
    _write_epub(saved)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "book-1",
        "revision_id": "rev-1", "title": "Existing book",
        "status": "complete", "format": "EPUB",
        "local_path": str(saved),
    })
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "historic",
        "title": "Historic owned book", "status": "pending",
    })
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("malformed rights must never acquire content"),
    )
    try:
        original = saved.read_bytes()
        result = service.sync_kobo_backup_account("vault-1")
        assert result["status"] == "partial"
        assert result["errors"] == 0
        assert result["malformed_manifest_items"] == 1
        assert result["downloaded"] == result["attempted"] == 0
        assert result["legacy_pruned"] == 0
        assert result["skipped"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "historic") is not None
        original_row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert original_row["status"] == "complete"
        assert original_row["local_path"] == str(saved)
        assert saved.read_bytes() == original

        checkpoint = json.loads(service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")
        ))
        assert checkpoint["status"] == "partial"
        assert checkpoint["processed"] == 1
        assert checkpoint["counts"]["malformed_manifest_items"] == 1

        preview = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert preview["manifest_valid"] is False
        assert preview["counts"]["invalid_manifest_items"] == 1
        assert preview["counts"]["potentially_missing"] == 0
        assert preview["acquisition_authorized"] is False
    finally:
        service.close()


def test_legacy_missing_flags_remain_valid_but_explicit_false_is_not(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[{
        "product_id": "owned-legacy", "title": "Legacy",
        "content_type": "ebook", "downloadable": False, "archived": False,
        # Older personal-library responses may omit the 'owned' flag.
    }])
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("downloadable=False must skip acquisition"),
    )
    try:
        assert _kobo_manifest_flags_invalid({"owned": None, "archived": False}) is False
        assert _kobo_manifest_flags_invalid({"owned": False}) is True
        assert _kobo_manifest_flags_invalid({"downloadable": None}) is False
        assert _kobo_manifest_flags_invalid({"downloadable": False}) is False
        assert _kobo_manifest_flags_invalid({"archived": True}) is False

        result = service.sync_kobo_backup_account("vault-1")
        assert result["status"] == "complete"
        assert result["malformed_manifest_items"] == 0
        assert result["downloaded"] == result["attempted"] == 0
        assert result["skipped"] == 1
        preview = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert preview["manifest_valid"] is True
        assert preview["counts"]["not_downloadable"] == 1
    finally:
        service.close()
