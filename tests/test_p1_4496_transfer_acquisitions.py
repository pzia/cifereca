"""P1 #4496: new inter-identity files land in Acquisitions with readable names.

All transactions run on disposable local SQLite and synthetic bytes.
Historical shared/ copies must remain in place and no supplier rights move.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cifereca.core.transfer_storage_policy import (
    directory_for_storage_class, transfer_readable_filename,
)
from tests.test_core0925_open_destinations import _service
from tests.test_core_p1_3275_safe_transfer import _source


@pytest.mark.parametrize("title,author,expected", [
    ("Les Trois Mousquetaires", "Alexandre Dumas", "Alexandre Dumas - Les Trois Mousquetaires.epub"),
    ("L'ouvrage", None, "L'ouvrage.epub"),
    (None, None, "Book.epub"),
    ("../../autre\\\\profil/secret", "../Alice", "Alice - autre profil secret.epub"),
    ("..", None, "Book.epub"),
    ("", "Auteur", "Book.epub"),
    (99, 99, "Book.epub"),
])
def test_closed_readable_filename_without_traversal(title, author, expected):
    name = transfer_readable_filename(
        Path("/profile/local/Book.epub"), title=title, author=author,
    )
    assert name == expected
    assert Path(name).name == name
    assert "/" not in name and "\\" not in name
    assert name not in (".", "..")
    assert len(name) < 256


def test_unknown_extension_not_used_as_a_catalogue_format():
    assert transfer_readable_filename(
        Path("file.weird-suffix"), title="Titre", author="Auteur",
    ) == "Auteur - Titre"
    assert transfer_readable_filename(
        Path("file.EPUB"), title="Titre",
    ) == "Titre.EPUB"


def test_real_send_uses_acquisitions_readable_name_and_no_rights_transfer(tmp_path):
    service = _service(tmp_path)
    source, placement = _source(service, tmp_path)
    try:
        before_rights = service.storage.entitlements()
        before_acquisitions = service.storage.acquisitions()
        receipt = service.materialize_to_identity(placement, "bob")
        dest = Path(receipt["path"])
        assert dest == tmp_path / "EBooks" / "bob" / "Acquisitions" / "Author - Book.epub"
        assert dest.read_bytes() == source.read_bytes()
        assert service.storage.entitlements() == before_rights
        assert service.storage.acquisitions() == before_acquisitions
        assert service.storage.placement_get(receipt["placement_id"])["identity_id"] == "bob"
        assert service.storage.placement_get(receipt["placement_id"])["metadata"]["storage_class"] == "transfer"
        repeated = service.materialize_to_identity(placement, "bob")
        assert repeated["status"] == "unchanged"
        assert repeated["path"] == str(dest)
        assert list(dest.parent.iterdir()) == [dest]
        assert not (dest.parent.parent / "shared").exists()
    finally:
        service.close()


def test_unrelated_destination_name_collision_never_overwrites(tmp_path):
    service = _service(tmp_path)
    source, placement = _source(service, tmp_path)
    dest = tmp_path / "EBooks" / "bob" / "Acquisitions" / "Author - Book.epub"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"recipient's unrelated work")
    try:
        receipt = service.materialize_to_identity(placement, "bob")
        sent = Path(receipt["path"])
        assert sent == dest.with_name("Author - Book (2).epub")
        assert dest.read_bytes() == b"recipient's unrelated work"
        assert sent.read_bytes() == source.read_bytes()
        assert service.materialize_to_identity(placement, "bob")["path"] == str(sent)
        assert len(list(dest.parent.iterdir())) == 2
    finally:
        service.close()


def test_multibyte_catalogue_name_fits_with_collision_suffix(tmp_path):
    service = _service(tmp_path)
    try:
        source, placement = _source(service, tmp_path)
        entry = next(row for row in service.storage.catalog_entries()
                     if row.get("placement_id") == placement)
        author, title = "編" * 96, "本" * 96
        service.storage.catalog_entry_put({**entry, "author": author, "title": title})
        dest_dir = tmp_path / "EBooks" / "bob" / "Acquisitions"
        dest_dir.mkdir(parents=True)
        occupied = dest_dir / transfer_readable_filename(source, author=author, title=title)
        occupied.write_bytes(b"recipient's unrelated work")

        receipt = service.materialize_to_identity(placement, "bob")
        dest = Path(receipt["path"])
        assert dest == occupied.with_name(occupied.stem + " (2).epub")
        assert dest.name.startswith("編") and " - 本" in dest.name
        assert len(os.fsencode(dest.name)) <= os.pathconf(dest_dir, "PC_NAME_MAX")
        assert dest.read_bytes() == source.read_bytes()
        assert occupied.read_bytes() == b"recipient's unrelated work"
        assert service.materialize_to_identity(placement, "bob")["path"] == str(dest)
    finally:
        service.close()


def test_history_is_not_reclassified_by_directory_mapping():
    assert directory_for_storage_class("transfer") == "Acquisitions"
