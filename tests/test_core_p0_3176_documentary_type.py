"""#3176: documentary advice is an exact local OPF claim, not authority."""
import pytest

from cifereca.gui.library_organizer_line import _display
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, rows
from tests.test_core_p0_3176_organizer_review import _put_epub, _service, _run
from tests.test_core_p0_4030_epub_evidence import META


def _type(value):
    return '<dc:type xmlns:dc="http://purl.org/dc/elements/1.1/">' + value + '</dc:type>'


@pytest.mark.parametrize("types,category", [
    (("https://schema.org/Book",), "Livres"),
    (("http://schema.org/ComicStory",), "BD-Manga"),
    (("https://schema.org/ComicIssue", "https://schema.org/ComicStory"), "BD-Manga"),
    (("https://schema.org/DigitalDocument",), "Documents"),
    (("https://schema.org/Book", "https://schema.org/ComicIssue"), "Non classés"),
    (("https://schema.org/Book", "Text"), "Non classés"),
    (("https://schema.org/Audiobook",), "Non classés"),
    (("Comic",), "Non classés"),
    ((), "Non classés"),
    (("https://schema.org/Book",) * 17, "Non classés"),
])
def test_category_requires_unambiguous_explicit_opf_type(store, tmp_path, types, category):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    # Existing path is deliberately misleading, never classification evidence.
    path, _ = _put_epub(store, root, "BD-Manga/Autrice/Comic.epub",
                        metadata=META + "".join(map(_type, types)))
    before = rows(store)
    original = path.read_bytes()
    _, result = _run(_service(store, root))
    group, = result["groups"]
    assert group["category_advice"] == category
    assert group["category_evidence"] == (
        None if category == "Non classés" else "verified_opf_type_claim")
    assert not group["relocation_proposed"]
    assert not result["classification_attested"]
    assert category in _display(result)
    assert rows(store) == before
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


def test_unverified_bytes_cannot_supply_category(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    path, _ = _put_epub(store, root, "Livres/old.epub",
                        metadata=META + _type("https://schema.org/Book"))
    path.write_bytes(b"tampered")
    _, result = _run(_service(store, root))
    group, = result["groups"]
    assert group["category_advice"] == "Non classés"
    assert group["category_evidence"] is None
    assert group["files"][0]["reason"] == "physical_unverified"
    assert path.read_bytes() == b"tampered"
