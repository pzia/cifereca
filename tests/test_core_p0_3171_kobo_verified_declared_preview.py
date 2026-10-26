"""P0 #3171: a checked Kobo declaration is present locally, not a receipt.

All files/accounts are synthetic; no provider content request.
"""
from __future__ import annotations

from cifereca.core import library_declared_presence as presence
from tests.test_core_p0_3171_kobo_backup_declared_guard import _declared_kobo


def _fresh(service):
    return service.kobo_backup_preview(
        "vault-1", refresh_manifest=True, verify_local_files=True,
    )


def test_verified_declared_kobo_epub_is_counted_as_local_without_backup_receipt(
    tmp_path, monkeypatch,
):
    service, book, scope, _ = _declared_kobo(tmp_path, monkeypatch)
    monkeypatch.setattr(presence, "acquisition_lock_file", lambda: tmp_path / "presence.lock")
    original = book.read_bytes()
    try:
        for _ in range(2):
            preview = _fresh(service)
            assert preview["counts"]["already_local_declared"] == 1
            assert preview["counts"]["attention_required"] == 0
            assert preview["counts"]["already_local_verified"] == 0
            assert preview["counts"]["potentially_missing"] == 0
            assert preview["blocking_items"] == 0
            assert preview["acquisition_authorized"] is False
            assert preview["examples"] == []
        assert book.read_bytes() == original
        assert service.storage.placements() == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()


def test_missing_or_changed_declared_kobo_file_remains_attention_not_missing(
    tmp_path, monkeypatch,
):
    service, book, _scope, _ = _declared_kobo(tmp_path, monkeypatch)
    monkeypatch.setattr(presence, "acquisition_lock_file", lambda: tmp_path / "presence.lock")
    try:
        book.write_bytes(b"changed synthetic EPUB bytes")
        view = _fresh(service)
        assert view["counts"]["already_local_declared"] == 0
        assert view["counts"]["attention_required"] == 1
        assert view["counts"]["potentially_missing"] == 0
        assert view["blocking_items"] == 1
        book.unlink()
        view = _fresh(service)
        assert view["counts"]["already_local_declared"] == 0
        assert view["counts"]["attention_required"] == 1
        assert view["counts"]["potentially_missing"] == 0
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()


def test_unverified_preview_does_not_promote_declared_link(tmp_path, monkeypatch):
    service, book, _scope, _ = _declared_kobo(tmp_path, monkeypatch)
    monkeypatch.setattr(presence, "acquisition_lock_file", lambda: tmp_path / "presence.lock")
    try:
        view = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        # Offline/unverified views retain their historical schema exactly.
        assert "already_local_declared" not in view["counts"]
        assert view["counts"]["attention_required"] == 1
        assert book.is_file()
    finally:
        service.close()
