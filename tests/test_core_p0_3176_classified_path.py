"""#3176: classified destination is layout advice only, never a relocation."""
import pytest

from cifereca.gui.library_organizer_line import _display
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, rows
from tests.test_core_p0_3176_organizer_review import _put_epub, _service, _run
from tests.test_core_p0_4030_epub_evidence import META

TYPED = META + '<dc:type xmlns:dc="http://purl.org/dc/elements/1.1/">https://schema.org/Book</dc:type>'


@pytest.mark.parametrize("state,reason,expected", [
    ("missing", "classified_directory_missing", True),
    ("available", "classified_layout_advice_only", True),
    ("file", "classified_target_occupied", False),
    ("sql", "classified_target_occupied", False),
    ("alias", "classified_target_unverified", False),
    ("nondirectory", "classified_target_unverified", False),
    ("history", "classified_source_not_admitted", False),
    ("unclassified", "classified_evidence_unavailable", False),
    ("duplicate", None, False),
])
def test_classified_path_only_for_unique_verified_acquisition(
    store, tmp_path, state, reason, expected,
):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    source, pid = _put_epub(
        store, root, "shared/old.epub" if state == "history" else "Acquisitions/old.epub",
        metadata=META if state == "unclassified" else TYPED,
    )
    target = root / "A" / "Livres" / "Autrice" / "Autrice - Roman commun.epub"
    if state in ("available", "file"):
        target.parent.mkdir(parents=True)
        if state == "file":
            target.write_bytes(b"foreign bytes")
    elif state == "sql":
        # A missing path is still reserved by an operational Placement.
        store.placement_put({"identity_id": "A", "state": "missing", "path": str(target)})
    elif state == "alias":
        external = tmp_path / "external"
        external.mkdir()
        (root / "A" / "Livres").symlink_to(external, target_is_directory=True)
    elif state == "nondirectory":
        (root / "A" / "Livres").write_bytes(b"not a directory")
    elif state == "duplicate":
        duplicate = source.with_name("copy.epub")
        duplicate.write_bytes(source.read_bytes())
        store.placement_put({"identity_id": "A", "state": "present",
                             "artifact_id": store.placement_get(pid)["artifact_id"],
                             "path": str(duplicate)})
    before = rows(store)
    tree = sorted(str(p) for p in root.rglob("*"))
    original = source.read_bytes()
    _, result = _run(_service(store, root))
    file = next(f for group in result["groups"] for f in group["files"] if f["placement"] == pid)
    assert file["classified_path_reason"] == reason
    proposed = "Livres/Autrice/Autrice - Roman commun.epub"
    assert file["classified_path_advice"] == (proposed if expected else None)
    assert (proposed in _display(result)) is expected
    assert not any(group["relocation_proposed"] for group in result["groups"])
    assert rows(store) == before and tree == sorted(str(p) for p in root.rglob("*"))
    assert source.read_bytes() == original
    if state == "file":
        assert target.read_bytes() == b"foreign bytes"
