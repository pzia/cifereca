"""#2835: equal bytes can carry different physical EPUB name conventions."""
from hashlib import sha256
from pathlib import Path, PurePath

import pytest

from cifereca.artifact_runtime import _placement_ref
from cifereca.catalog import LocalCatalog
from cifereca.filename_suffixes import epub_filename_convention
from cifereca.storage import Storage
from tests.test_p1_2854_usb_content_review import _declared_epub


@pytest.mark.parametrize("name,expected", [
    ("Book.epub", "epub"), ("Book.KEPUB.EPUB", "kepub"),
    ("Book.FXL.KEPUB.EPUB", "fxl.kepub"),
    ("Book.kepub.notes.epub", "epub"), ("Book.pdf", None),
    ("Book", None), (".epub", None),
])
def test_descriptive_name_convention(name, expected):
    assert epub_filename_convention(PurePath(name)) == expected


@pytest.mark.parametrize("first,second,initial", [
    (".epub", ".fxl.kepub.epub", "epub"),
    (".kepub.epub", ".epub", "kepub"),
    (".fxl.kepub.epub", ".kepub.epub", "kepub"),
])
def test_targeted_and_full_rescan_do_not_reclassify_shared_sha_from_another_path(
    tmp_path, first, second, initial,
):
    root = tmp_path / "library"
    root.mkdir()
    a = root / ("A" + first)
    b = root / ("B" + second)
    data = _declared_epub("pre-paginated")
    a.write_bytes(data)
    b.write_bytes(data)
    aid = "sha256:" + sha256(data).hexdigest()
    storage = Storage(tmp_path / "state.sqlite")
    try:
        catalog = LocalCatalog(storage, root)
        one = catalog.index_path(a)
        assert one["status"] == "indexed"
        assert storage.artifact_get(aid)["media_format"] == initial
        two = catalog.index_path(b)
        assert two["status"] == "indexed"
        assert one["artifact_id"] == two["artifact_id"] == aid
        assert storage.artifact_get(aid)["media_format"] == initial
        for _ in range(2):
            assert catalog.scan().cataloged == 2
            assert storage.artifact_get(aid)["media_format"] == initial
        for path, suffix in [(a, first), (b, second)]:
            stored = storage.placement_for_path(path)
            assert stored["artifact_id"] == aid and stored["state"] == "present"
            assert stored["identity_id"] is None and stored["provider_id"] is None
            placement = _placement_ref(stored)
            assert placement.filename == path.name
            assert placement.facts["epub_filename_convention"] == suffix[1:].removesuffix(".epub")
            assert path.read_bytes() == data
        assert len(storage.placements()) == 2
        assert len(storage.catalog_entries()) == 2
        with storage._lock, storage._connect() as db:
            assert db.execute("SELECT count(*) FROM artifacts").fetchone() == (1,)
            assert db.execute("SELECT count(*) FROM origin_links").fetchone() == (0,)
    finally:
        storage.close()


def test_catalog_filename_does_not_override_existing_explicit_fxl_format(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "Book.epub"
    data = _declared_epub("pre-paginated")
    path.write_bytes(data)
    digest = sha256(data).hexdigest()
    row = dict(artifact_id="sha256:" + digest, sha256=digest, size=len(data),
               media_format="fxl.kepub", mime_type="application/epub+zip")
    storage = Storage(tmp_path / "state.sqlite")
    try:
        storage.artifact_put(row)
        assert LocalCatalog(storage, root).index_path(path)["status"] == "indexed"
        assert storage.artifact_get(row["artifact_id"])["media_format"] == "fxl.kepub"
        # The filename-only guard does not change authoritative callers.
        storage.artifact_put({**row, "media_format": "epub"})
        assert storage.artifact_get(row["artifact_id"])["media_format"] == "epub"
    finally:
        storage.close()


@pytest.mark.parametrize("state,kind,expected", [
    ("present", "file", "fxl.kepub"), ("missing", "file", None),
    ("invalid", "file", None), ("present", "bundle", None),
])
def test_api_does_not_trust_stored_convention_claim(state, kind, expected):
    row = dict(placement_id="p", artifact_id="a", identity_id="Alice",
               state=state, path="/private/A/Book.fxl.kepub.epub",
               metadata={"artifact_kind": kind, "epub_filename_convention": "FORGED"})
    ref = _placement_ref(row)
    assert ref.facts.get("epub_filename_convention") == expected
    assert "FORGED" not in repr(ref.facts)
