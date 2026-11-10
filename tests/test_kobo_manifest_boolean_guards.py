"""Kobo P0: raw manifest rights flags must be real JSON booleans.

A malformed/denied ownership flag cannot authorize a download, create a
backup row, silently mark the source complete or prune previous rights.
"""
from __future__ import annotations

import json

import pytest

from cifereca.providers import ProviderConfigurationError

from cifereca.core.kobo_backup import (
    _kobo_checkpoint_key,
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
        # Raw 0.7 flags are not canonical A evidence. D3 cannot GET.
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_w2_retired_use_canonical_provider",
        ):
            service.sync_kobo_backup()
        assert service.storage.backup_get(
            "kobo", "vault-1", "book-1",
        )["status"] == "complete"
        assert service.storage.backup_get("kobo", "vault-1", "historic") is not None
        original_row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert original_row["status"] == "complete"
        assert original_row["local_path"] == str(saved)
        assert saved.read_bytes() == original

        assert service.storage.metadata_get(
            _kobo_checkpoint_key("default", "vault-1")
        ) is None
        with pytest.raises(
            ProviderConfigurationError,
            match="canonical_kobo_preview_abi_unavailable",
        ):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
    finally:
        service.close()


def test_retired_w2_cannot_consume_partial_historical_manifest(tmp_path, monkeypatch):
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

        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_w2_retired_use_canonical_provider",
        ):
            service.sync_kobo_backup()
        with pytest.raises(
            ProviderConfigurationError,
            match="canonical_kobo_preview_abi_unavailable",
        ):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
    finally:
        service.close()
