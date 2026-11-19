"""#4030: exact OPF evidence, no cache and no title inferred from filename."""
import hashlib
import pytest

from cifereca.core import local_origin_suggestion_preview as preview
from cifereca.core.local_origin_epub_evidence import read_epub_description
from tests.test_core_p0_2835_epub_layout import _book
from tests.test_core_p0_3171_atomic_publication_v14 import store, register
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public

META = '<dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Roman commun</dc:title>' + \
       '<dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Autrice</dc:creator>'


def test_opf_only_immutable_stream():
    stream = _book(META)
    before = stream.getvalue()
    assert read_epub_description(stream) == ("Roman commun", ("Autrice",))
    assert stream.tell() == 3 and stream.getvalue() == before


@pytest.mark.parametrize("case", ["multiple_titles", "other_namespace", "multi_package", "unsafe_member"])
def test_ambiguous_opf_refused(case):
    if case == "multiple_titles":
        stream = _book(META + META)
    elif case == "other_namespace":
        stream = _book('<title>Roman commun</title>')
    elif case == "multi_package":
        stream = _book(META, roots='<rootfile full-path="OPS/content.opf"/><rootfile full-path="OPS/other.opf"/>')
    else:
        stream = _book(META, roots='<rootfile full-path="../OPS/content.opf"/>')
    assert read_epub_description(stream) is None


def _local_epub(store, tmp_path, metadata):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = root / "A" / "unrelated-name.epub"
    path.parent.mkdir(parents=True)
    data = _book(metadata).getvalue()
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    aid = "sha256:" + digest
    store.artifact_put({"artifact_id": aid, "sha256": digest,
                        "size": len(data), "media_format": "epub"})
    pid = store.placement_put({"artifact_id": aid, "identity_id": "A",
                               "path": str(path), "state": "present"})
    persist(store, verified_public(record_id="public-record", title="Roman commun",
                                   author="Autrice"), at=100)
    return root, path, pid


def test_opf_with_no_catalogue_entry_suggests_without_origin(store, tmp_path):
    root, path, pid = _local_epub(store, tmp_path, META)
    before = path.read_bytes()
    result = preview.suggest_local_placement_documents(
        store, library_root=root, identity_id="A", placement_id=pid,
        explicitly_requested=True,
    )
    assert result["metadata_evidence"] == "verified_epub_opf_description"
    hit, = result["suggestions"].suggestions
    assert hit.comparison == "title_author_concordant"
    assert not result["association_authorized"]
    assert path.read_bytes() == before


def test_no_catalogue_fallback_for_ambiguous_epub(store, tmp_path):
    root, path, pid = _local_epub(store, tmp_path, META + META)
    report = preview.suggest_local_placement_documents(
        store, library_root=root, identity_id="A", placement_id=pid,
        explicitly_requested=True,
    )
    assert report["reason"] == "epub_description_unverified"
    assert report["suggestions"] is None
