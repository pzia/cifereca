"""#4276 generic Core/Plugins contract; no supplier network or P0 sync mutation."""
from dataclasses import replace

import pytest

from cifereca.models import SearchResult
from cifereca.plugin_api import (
    DOCUMENT_CLASSIFICATION_CONTRACT,
    ClassificationEvidence,
    DocumentClassification,
    classification_for_search_result,
)


def fact(value, source="Resource.Type", raw=None):
    return ClassificationEvidence(
        value=value, status="attested", source_key=source, raw_code=raw,
    )


def row(**kwargs):
    values = dict(
        id="fixture", record_id="fixture", docbase="SYRACUSE",
        medium="physical", title="Fixture", author=None, type=None,
        format=None, publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="CATALOGUE",
    )
    values.update(kwargs)
    return SearchResult(**values)


@pytest.mark.parametrize(
    "medium,kind,meta",
    [
        ("physical", "book", "PRINTED_BOOK"),
        ("physical", "comics", "COMIC"),
        ("physical", "music", "MUSIC"),
        ("physical", "video", "VIDEO"),
        ("physical", "periodical", "PERIODICAL"),
        ("digital", "book", "EBOOK"),
        ("digital", "audiobook", "AUDIOBOOK"),
        ("digital", "comics", "COMIC"),
        ("digital", "audio", "UNKNOWN"),
    ],
)
def test_independent_document_nature_and_medium(medium, kind, meta):
    projection = DocumentClassification(medium=fact(medium), document_kind=fact(kind))
    assert projection.meta_type == meta
    assert projection.matches_filter(medium)
    assert not projection.matches_filter("digital" if medium == "physical" else "physical")
    assert projection.matches_filter("all")
    assert projection.matches_filter("ebook") == (meta == "EBOOK")
    assert projection.matches_filter("printed_book") == (meta == "PRINTED_BOOK")


def test_syracuse_codes_are_never_file_formats_or_permissions():
    physical = DocumentClassification(
        medium=fact("physical", "Resource.RscBase", "SYRACUSE"),
        document_kind=fact("video", "Resource.Type", "Film"),
        material_format=fact("dvd", "Holding.Media", "DVD"),
    )
    assert physical.meta_type == "VIDEO"
    assert physical.material_format.value == "dvd"
    assert physical.digital_format.value == "unknown"
    assert physical.offered_file_formats == ()
    assert not physical.matches_filter("printed_book")
    with pytest.raises(ValueError):
        DocumentClassification(
            medium=fact("digital"), document_kind=fact("book"),
            digital_format=fact("mone"),  # Syracuse Frmt; not EPUB.
        )


def test_known_storyplayr_ebook_never_infers_dilicom_or_loan_right():
    notice = DocumentClassification(
        medium=fact("digital", "Resource.Type", "Livre numérique"),
        document_kind=fact("book", "Resource.Type", "Livre numérique"),
    )
    result = row(
        docbase="STORYPLAYR", medium="digital", scenario="CATALOGUE",
        format="MONE", document_classification=notice,
    )
    assert classification_for_search_result(result).meta_type == "EBOOK"
    assert notice.matches_filter("digital")
    assert not notice.matches_filter("physical")
    assert result.docbase == "STORYPLAYR"
    assert result.downloadable is None
    assert result.owned is None
    assert notice.offered_file_formats == ()


def test_legacy_medium_and_ebook_route_are_not_attestation():
    legacy = row(medium="physical", scenario="EBOOKS", type="Film", format="AUVI")
    projection = classification_for_search_result(legacy)
    assert DOCUMENT_CLASSIFICATION_CONTRACT.endswith("/1")
    assert projection.medium.value == "unknown"
    assert projection.document_kind.value == "unknown"
    assert projection.meta_type == "UNKNOWN"
    assert projection.matches_filter("all")
    assert not projection.matches_filter("physical")
    assert not projection.matches_filter("digital")
    assert not projection.matches_filter("ebook")


def test_preference_ebook_does_not_fill_unknown_document_data():
    assert DocumentClassification().meta_type == "UNKNOWN"
    bounded = DocumentClassification(
        medium=ClassificationEvidence("digital", "inferred_bounded"),
        document_kind=fact("book"),
    )
    assert bounded.meta_type == "UNKNOWN"
    conflict = DocumentClassification(
        medium=ClassificationEvidence(status="conflict"),
        document_kind=fact("book"),
    )
    assert conflict.meta_type == "UNKNOWN"
    assert conflict.matches_filter("all")


def test_evidence_and_format_compatibility_fail_closed():
    with pytest.raises(ValueError):
        ClassificationEvidence("book", "unknown")
    with pytest.raises(ValueError):
        ClassificationEvidence("unknown", "attested")
    with pytest.raises(ValueError):
        ClassificationEvidence("book", "unreviewed")
    with pytest.raises(ValueError):
        DocumentClassification(medium=fact("physical"), digital_format=fact("epub"))
    with pytest.raises(ValueError):
        DocumentClassification(medium=fact("digital"), material_format=fact("cd"))
    with pytest.raises(ValueError):
        DocumentClassification(
            medium=fact("digital"), offered_file_formats=("epub", "epub"),
        )
    with pytest.raises(ValueError):
        DocumentClassification(
            medium=fact("digital"), offered_file_formats=("mone",),
        )


def test_offered_file_format_is_not_received_artifact_or_entitlement():
    projection = DocumentClassification(
        medium=fact("digital"),
        document_kind=fact("book"),
        digital_format=fact("epub", "Offer.Format", "EPUB"),
        offered_file_formats=("epub", "pdf"),
    )
    assert projection.meta_type == "EBOOK"
    assert projection.offered_file_formats == ("epub", "pdf")
    assert not hasattr(projection, "sha256")
    assert not hasattr(projection, "placement")
    assert not hasattr(projection, "rights")
    assert not hasattr(projection, "can_borrow")
    assert replace(projection, offered_file_formats=()).meta_type == "EBOOK"


def test_raw_upstream_code_is_not_in_diagnostic_repr():
    proof = fact("book", "Resource.Type", "source-sensitive-raw-field")
    assert "source-sensitive-raw-field" not in repr(proof)
