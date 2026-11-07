"""P0 #3196: a 0.7 complete row never reads another identity's book.

Synthetic SQLite/EPUB and provider manifest. No supplier I/O, deletion or
new canonical rights: the old receipt is quarantined for manual reconciliation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_kobo_backup_guard import _service, _write_epub


@pytest.mark.parametrize("case", ["file_symlink", "parent_symlink", "foreign_absolute"])
def test_legacy_complete_backup_never_accepts_foreign_profile_file(
    tmp_path, monkeypatch, case,
):
    service = _service(tmp_path)
    target = service._kobo_backup_target_dir("alice")
    foreign = service.library_dir() / "bob" / "kobo" / "old.epub"
    _write_epub(foreign, b"private-foreign")
    before = foreign.read_bytes()

    if case == "file_symlink":
        alias = target / "old.epub"
        alias.symlink_to(foreign)
    elif case == "parent_symlink":
        alias_parent = target / "old-links"
        alias_parent.symlink_to(foreign.parent, target_is_directory=True)
        alias = alias_parent / "old.epub"
    else:
        alias = foreign

    assert alias.is_file()
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "alice", "account_id": "account",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Private source", "format": "EPUB",
        "status": "complete", "local_path": str(alias),
        "size": len(before), "sha256": hashlib.sha256(before).hexdigest(),
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *_a, **_k: {"scanned": 0})
    monkeypatch.setattr(
        service, "_observed_backup_artifact",
        lambda *_a, **_k: pytest.fail("foreign bytes must never be inspected"),
    )
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_k: pytest.fail("foreign backup must not trigger download"),
    )
    try:
        summary = service.sync_kobo_backup()
        assert summary["unchanged"] == summary["attempted"] == summary["downloaded"] == 0
        assert summary["reconcile_skipped"] >= 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] == "reconcile_required"
        assert foreign.read_bytes() == before
        with service.storage._lock, service.storage._connect() as sql:
            assert sql.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
            assert sql.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 0
    finally:
        service.close()


def test_legacy_complete_backup_still_accepts_trusted_flat_library_file(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    path = service.library_dir() / "older-flat.epub"
    _write_epub(path, b"flat-v0.7")
    payload = path.read_bytes()
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "alice", "account_id": "account",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Flat legacy", "format": "EPUB",
        "status": "complete", "local_path": str(path),
        "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *_a, **_k: {"scanned": 0})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_k: pytest.fail("complete flat historical book must not download"),
    )
    try:
        result = service.sync_kobo_backup()
        assert result["unchanged"] == 0
        assert result["reconcile_pending"] >= 1
        assert result["attempted"] == result["downloaded"] == 0
        assert path.read_bytes() == payload
    finally:
        service.close()
