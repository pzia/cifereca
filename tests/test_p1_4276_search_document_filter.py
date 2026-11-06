"""#4276: consumer-facing documentary facets without changing supplier or P0 Sync.

Synthetic document facts only; no Paris connection, ADEPT, network, rights or
filesystem. The legacy model defaults remain deliberately contradictory.
"""
from dataclasses import asdict

import pytest

from cifereca.document_classification import (
    ClassificationEvidence, DocumentClassification,
)
from cifereca.core.document_filter import (
    classification_from_row, classified_content_type,
    filter_loaded_with_documentary_proofs, matches_classified_type,
    loaded_document_facets, public_document_classification, search_medium_label,
)
from cifereca.gui.query_model import QueryState, result_row
from cifereca.models import SearchResult


def attested(value):
    return ClassificationEvidence(value, "attested", "Resource.Type", "SECRET_RAW_CODE")


def classified(medium, kind, *, offered=()):
    return DocumentClassification(
        medium=attested(medium),
        document_kind=attested(kind),
        offered_file_formats=tuple(offered),
    )


def notice(name, *, medium="physical", old_kind="physical", doc=None):
    return SearchResult(
        id=name, record_id=name, docbase="SYRACUSE",
        medium=medium, title="Fixture " + name, author=None,
        type="Source Type", format="MONE", publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="CATALOGUE", content_type=old_kind,
        document_classification=doc,
    )


def test_local_document_facets_agree_with_search_on_typed_and_legacy_notices():
    rows = [
        notice("epub", doc=classified("digital", "book", offered=("epub",))),
        notice("print", doc=classified("physical", "book")),
        notice("unknown", doc=DocumentClassification()),
        {"format": "KEPUB"},
        {"format": "EPUB", "document_classification": {"contract": []}},
    ]
    assert [loaded_document_facets(row, ("epub", "pdf")) for row in rows] == [
        ("digital", ("epub",)), ("physical", ()), ("unknown", ()),
        ("digital", ("epub",)), ("unknown", ()),
    ]


def test_documentary_type_dominates_historic_catalogue_physical_guess():
    ebook = result_row(notice(
        "ebook", medium="physical", old_kind="physical",
        doc=classified("digital", "book", offered=("epub",)),
    ))
    film = result_row(notice(
        "film", medium="physical", old_kind="physical",
        doc=classified("physical", "video"),
    ))
    rows = [ebook, film]
    assert [r["record_id"] for r in QueryState(scope="digital").filter(rows)] == ["ebook"]
    assert [r["record_id"] for r in QueryState(scope="physical").filter(rows)] == ["film"]
    assert [r["record_id"] for r in QueryState(types=("ebook",)).filter(rows)] == ["ebook"]
    assert [r["record_id"] for r in QueryState(types=("video",)).filter(rows)] == ["film"]
    assert [r["record_id"] for r in QueryState(types=("epub",)).filter(rows)] == ["ebook"]
    assert [r["record_id"] for r in QueryState(types=("physical",)).filter(rows)] == ["film"]
    assert [r["record_id"] for r in QueryState().filter(rows)] == ["ebook", "film"]


@pytest.mark.parametrize("kind,medium,expected", [
    ("book", "digital", "Livre numérique"),
    ("book", "physical", "Livre imprimé"),
    ("comics", "physical", "Bande dessinée · physique"),
    ("music", "physical", "Musique · physique"),
    ("video", "physical", "Film / vidéo · physique"),
    ("periodical", "physical", "Revue · physique"),
    ("video", "digital", "Vidéo · numérique"),
    ("audiobook", "digital", "Livre audio"),
])
def test_fixed_documentary_labels_do_not_call_everything_a_book(kind, medium, expected):
    assert search_medium_label(classified(medium, kind)) == expected


def test_unknown_and_conflict_cannot_be_promoted_by_legacy_format_or_route():
    conflict = DocumentClassification(
        medium=ClassificationEvidence(status="conflict"),
        document_kind=attested("book"),
    )
    row = result_row(notice("conflict", medium="digital", old_kind="ebook", doc=conflict))
    row["format"] = "EPUB"
    assert classified_content_type(conflict) == "unknown"
    assert [r["record_id"] for r in QueryState(scope="unknown").filter((row,))] == ["conflict"]
    assert QueryState(scope="digital").filter((row,)) == []
    assert QueryState(types=("ebook",)).filter((row,)) == []
    assert QueryState(types=("epub",)).filter((row,)) == []
    assert search_medium_label(conflict) == "Support indéterminé"


def test_source_raw_evidence_never_in_ui_result_row_or_public_dto():
    raw = result_row(notice("redacted", doc=classified("physical", "video")))
    assert "SECRET_RAW_CODE" not in repr(raw)
    assert "source_key" not in repr(raw)
    assert "raw_code" not in repr(raw)
    typed = classification_from_row(raw)
    assert typed is not None and typed.meta_type == "VIDEO"
    assert public_document_classification(typed) == raw["document_classification"]


def test_dataclass_asdict_compatibility_and_malformed_projection_fail_closed():
    obj = notice("dict-bridge", medium="physical",
                 doc=classified("digital", "book"))
    full = asdict(obj)  # historic callers serialize SearchResult this way.
    assert classification_from_row(full).meta_type == "EBOOK"
    assert QueryState(scope="digital").filter((full,))
    malformed = dict(full)
    malformed["document_classification"] = {
        "medium": {"value": "digital", "status": "attested"},
        "document_kind": {"value": "book", "status": "attested"},
        "digital_format": {"value": "mone", "status": "attested"},
        "material_format": {"value": "unknown", "status": "unknown"},
        "offered_file_formats": ("epub",),
    }
    parsed = classification_from_row(malformed)
    assert parsed is not None and parsed.meta_type == "UNKNOWN"
    assert QueryState(scope="digital").filter((malformed,)) == []
    assert QueryState(scope="unknown").filter((malformed,))


def test_legacy_filtering_unchanged_if_no_new_contract():
    old_book = result_row(notice("old-paper"))
    old_audio = result_row(notice(
        "old-audio", medium="digital", old_kind="audiobook",
    ))
    assert old_book["document_classification"] is None
    assert [r["record_id"] for r in QueryState(scope="physical").filter((old_book, old_audio))] == ["old-paper"]
    assert [r["record_id"] for r in QueryState(types=("audiobook",)).filter((old_book, old_audio))] == ["old-audio"]


def test_typed_format_is_offer_not_sha_local_file_or_right():
    book = classified("digital", "book", offered=("epub", "pdf"))
    assert matches_classified_type(book, "epub")
    assert matches_classified_type(book, "pdf")
    assert not matches_classified_type(book, "mp3")
    assert not hasattr(book, "rights")
    assert not hasattr(book, "placement")
    assert not hasattr(book, "sha256")


def test_strict_classification_prevents_physical_dvd_from_matching_epub():
    film = classified("physical", "video")
    assert not matches_classified_type(film, "epub")
    assert not matches_classified_type(film, "ebook")
    assert matches_classified_type(film, "physical")


def test_no_trust_in_untyped_status_or_external_metadata():
    row = result_row(notice("malicious", doc=classified("digital", "book")))
    row["document_classification"] = {
        **row["document_classification"],
        "extra_private_claim": "Bearer SECRET",
    }
    assert classification_from_row(row).meta_type == "UNKNOWN"
    assert QueryState(scope="digital").filter([row]) == []
    assert QueryState(scope="unknown").filter([row])


@pytest.mark.parametrize("medium,label", [
    ("physical", "Support physique · type de document indéterminé"),
    ("digital", "Support numérique · type de document indéterminé"),
])
def test_attested_medium_is_preserved_even_if_document_kind_is_unknown(medium, label):
    proof = DocumentClassification(medium=attested(medium))
    assert search_medium_label(proof) == label
    row = result_row(notice("untyped", medium="digital", doc=proof))
    assert QueryState(scope=medium).filter([row])
    other = "physical" if medium == "digital" else "digital"
    assert QueryState(scope=other).filter([row]) == []
    assert QueryState(types=("ebook",)).filter([row]) == []
