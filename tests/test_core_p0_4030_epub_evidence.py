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


@pytest.mark.parametrize("value,expected", [
    ("urn:isbn:0-306-40615-2", ("9780306406157",)),
    ("urn:isbn:978-0-306-40615-7", ("9780306406157",)),
    ("urn:isbn:0-8044-2957-X", ("9780804429573",)),
    ("urn:isbn:9780306406158", ()),
    ("urn:isbn:1234567890128", ()),
    ("9780306406157", ()),
    ("https://private.invalid/9780306406157", ()),
])
def test_only_typed_checksum_valid_isbn_claims_are_exposed(value, expected):
    from cifereca.core.local_origin_epub_evidence import read_epub_bibliography
    stream = _book(META + '<dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/">' +
                   value + '</dc:identifier>')
    before = stream.getvalue()
    assert read_epub_bibliography(stream) == ("Roman commun", ("Autrice",), expected)
    assert stream.tell() == 3 and stream.getvalue() == before


def test_epub_isbn_claims_deduplicated_but_distinct_editions_preserved(store, tmp_path):
    def identifier(value, attributes=""):
        return ('<dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/" '
                + attributes + '>' + value + '</dc:identifier>')
    metadata = (META + identifier("urn:isbn:0-306-40615-2")
                + identifier("urn:isbn:9780306406157")
                + identifier("9780140328721", 'xmlns:opf="http://www.idpf.org/2007/opf" opf:scheme="ISBN"')
                + identifier("9780804429573")
                + identifier("urn:isbn:9780306406158"))
    root, path, pid = _local_epub(store, tmp_path, metadata)
    before = path.read_bytes()
    result = preview.suggest_local_placement_documents(
        store, library_root=root, identity_id="A", placement_id=pid,
        explicitly_requested=True,
    )
    assert result["local_isbns"] == ("9780140328721", "9780306406157")
    assert result["isbn_catalogue_comparison_performed"] is False
    assert result["association_authorized"] is False
    hit, = result["suggestions"].suggestions
    assert hit.comparison == "title_author_concordant"
    assert path.read_bytes() == before


def test_isbn_overflow_does_not_choose_arbitrary_subset():
    from cifereca.core.local_origin_epub_evidence import read_epub_bibliography
    stream = _book(META + '<dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   'urn:isbn:9780306406157</dc:identifier>' * 33)
    assert read_epub_bibliography(stream) == ("Roman commun", ("Autrice",), ())


def _identifier(value, ident="isbn", scheme=""):
    namespace = 'xmlns:opf="http://www.idpf.org/2007/opf"'
    return (f'<dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'{namespace} id="{ident}" {scheme}>{value}</dc:identifier>')


def _refinement(code="15", *, ref="#isbn", scheme="onix:codelist5"):
    return f'<meta refines="{ref}" property="identifier-type" scheme="{scheme}">{code}</meta>'


@pytest.mark.parametrize("value,code,expected", [
    ("9780306406157", "15", ("9780306406157",)),
    ("0-306-40615-2", "02", ("9780306406157",)),
    ("0-306-40615-2", "15", ()),
    ("9780306406157", "02", ()),
    ("9780306406158", "15", ()),
    ("9780306406157", "01", ()),
])
def test_epub3_refined_isbn_requires_exact_type_and_checksum(value, code, expected):
    from cifereca.core.local_origin_epub_evidence import read_epub_bibliography
    stream = _book(META + _identifier(value) + _refinement(code))
    original = stream.getvalue()
    assert read_epub_bibliography(stream) == ("Roman commun", ("Autrice",), expected)
    assert stream.tell() == 3 and stream.getvalue() == original


@pytest.mark.parametrize("metadata,attributes", [
    (_identifier("9780306406157") + _refinement(ref="#other"), 'version="3.0"'),
    (_identifier("9780306406157") + _refinement(scheme="custom:codelist5"), 'version="3.0"'),
    (_identifier("9780306406157") + _refinement(), 'version="2.0"'),
    (_identifier("9780306406157") + _refinement(), 'version="3.0" prefix="onix: urn:foreign#"'),
    (_identifier("9780306406157", ident="p") + _refinement(ref="#p"), 'version="3.0"'),
    (_identifier("9780306406157") + _identifier("9780140328721") + _refinement(), 'version="3.0"'),
    (_identifier("9780306406157") + _refinement() + _refinement("02"), 'version="3.0"'),
    (_identifier("urn:isbn:9780306406157", scheme='opf:scheme="UUID"'), 'version="3.0"'),
    (_identifier("urn:isbn:9780306406157") + _refinement("01"), 'version="3.0"'),
    (_identifier("9780306406157") + _refinement() * 257, 'version="3.0"'),
])
def test_epub3_refinements_do_not_upgrade_ambiguous_claims(metadata, attributes):
    from cifereca.core.local_origin_epub_evidence import read_epub_bibliography
    assert read_epub_bibliography(_book(META + metadata, package_attrs=attributes)) == (
        "Roman commun", ("Autrice",), ())


def test_refined_isbn_evidence_survives_exact_local_review_without_right(store, tmp_path):
    root, path, pid = _local_epub(store, tmp_path,
                                 META + _identifier("9780306406157") + _refinement())
    before = path.read_bytes()
    result = preview.suggest_local_placement_documents(
        store, library_root=root, identity_id="A", placement_id=pid, explicitly_requested=True)
    assert result["local_isbns"] == ("9780306406157",)
    assert result["metadata_evidence"] == "verified_epub_opf_description"
    assert not result["isbn_catalogue_comparison_performed"]
    assert not result["provider_access_performed"] and not result["association_authorized"]
    assert not result["may_write_origin_or_right"] and path.read_bytes() == before

@pytest.mark.parametrize("field", ["title", "creator"])
@pytest.mark.parametrize("control", ["\u202e", "\u2066", "\u200d", "\u2028", "\x85"])
def test_opf_documentary_labels_refuse_invisible_controls(field, control):
    metadata = META.replace("Roman commun" if field == "title" else "Autrice",
                            "Label" + control + "Other")
    stream = _book(metadata)
    before = stream.getvalue()
    assert read_epub_description(stream) is None
    assert stream.tell() == 3 and stream.getvalue() == before
