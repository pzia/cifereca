"""Kobo destination collision safety: dangling symlinks are occupied paths."""
from pathlib import Path
from types import SimpleNamespace

from cifereca.core.kobo_backup import _kobo_destination


class _Storage:
    def backup_get(self, provider, credential, record_id):
        assert (provider, credential, record_id) == ("kobo", "credential", "book-12345678")
        return None


class _Acquisition:
    @staticmethod
    def _safe_filename(value):
        return value


def test_dangling_symlink_cannot_be_selected_as_kobo_download_destination(tmp_path):
    service = SimpleNamespace(storage=_Storage(), acquisition=_Acquisition())
    item = SimpleNamespace(author=None, title="Book", record_id="book-12345678", source_credential="credential")
    occupied = tmp_path / "Book.epub"
    occupied.symlink_to(tmp_path / "missing-target.epub")

    destination = _kobo_destination(service, item, tmp_path, "EPUB")

    assert destination == tmp_path / "Book [12345678].epub"
    assert destination != occupied
    assert occupied.is_symlink() and not occupied.exists()


def test_existing_regular_file_still_uses_compatibility_suffix(tmp_path):
    service = SimpleNamespace(storage=_Storage(), acquisition=_Acquisition())
    item = SimpleNamespace(author=None, title="Book", record_id="book-12345678", source_credential="credential")
    occupied = tmp_path / "Book.pdf"
    occupied.write_bytes(b"%PDF-1.7\\n")

    assert _kobo_destination(service, item, tmp_path, "PDF") == tmp_path / "Book [12345678].pdf"
    assert occupied.read_bytes() == b"%PDF-1.7\\n"
