"""P0 #4858 W2: a 0.7 complete+matching SHA never proves canonical B.

The historical receipt is not a SourceReference/OriginLink/Placement. Keep
the old bytes and full archive for supervised migration and never re-GET.
"""
from __future__ import annotations

import hashlib

import pytest

from cifereca.core.kobo_backup import _skip_manifest_item
from tests.test_kobo_backup_guard import _service, _write_epub, _manifest_row


def _historical(service):
    path = service._kobo_backup_target_dir("alice") / "old.epub"
    _write_epub(path, b"historic-unchanged")
    payload = path.read_bytes()
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "alice", "account_id": "account",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Book One", "format": "EPUB",
        "status": "complete", "local_path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    })
    return path, payload


def test_legacy_complete_matching_bytes_is_not_positive_w2_presence(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    path, payload = _historical(service)
    previous = service.storage.backup_get("kobo", "vault-1", "book-1")
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_k: pytest.fail("old complete must not re-GET"),
    )
    try:
        first = service.sync_kobo_backup_account("vault-1")
        assert first["unchanged"] == first["attempted"] == first["downloaded"] == 0
        assert first["reconcile_pending"] >= 1
        assert first["reconcile_skipped"] >= 1
        assert first["status"] == "attention_required"
        assert path.read_bytes() == payload
        current = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert current["status"] == "complete"  # archive, NOT canonical proof
        assert current["revision_id"] == previous["revision_id"]
        assert current["sha256"] == previous["sha256"]
        assert current["size"] == previous["size"]
        assert current["local_path"] == previous["local_path"]
        assert not service.storage.origin_links()
        assert not service.storage.source_references(provider_id="kobo")

        # No W2 positive and no second GET after reopening the same manifest.
        second = service.sync_kobo_backup_account("vault-1")
        assert second["unchanged"] == second["attempted"] == second["downloaded"] == 0
        assert second["reconcile_skipped"] >= 1
        assert path.read_bytes() == payload
    finally:
        service.close()


def test_matching_legacy_sha_is_read_only_at_w2_skip_boundary(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    path, payload = _historical(service)
    row = service.storage.backup_get("kobo", "vault-1", "book-1")
    event = []
    monkeypatch.setattr(
        service.storage, "backup_put",
        lambda *_a, **_kw: pytest.fail(
            "same-old SHA must not use the W1 mirror as a positive writer"
        ),
    )
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("no opaque second GET"),
    )
    summary = {"unchanged": 0, "reconcile_pending": 0, "reconcile_skipped": 0}
    try:
        assert _skip_manifest_item(
            service, ("rev-1", "Book One", "account", row, row),
            _manifest_row(), service._kobo_backup_target_dir("alice"),
            {}, True, {}, 0, 1, summary,
            lambda name, **kw: event.append((name, kw)),
        ) is True
        assert summary == {
            "unchanged": 0, "reconcile_pending": 1, "reconcile_skipped": 1,
        }
        assert event[0][0] == "reconcile_required"
        assert event[0][1]["code"] == "legacy_complete_without_canonical_origin"
        assert path.read_bytes() == payload
    finally:
        service.close()
