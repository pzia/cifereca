from __future__ import annotations

from cifereca.core.search import (
    available_formats,
    filter_loaded_results,
    project_acquisition_offer_formats,
)
from cifereca.plugin_api import (
    AcquisitionOffer,
    acquisition_offer_formats,
    acquisition_offer_preferred_format,
    acquisition_offers_metadata,
)
from cifereca.providers import ProviderDescriptor, ProviderSession


class Session(ProviderSession):
    def connect(self):
        self.connected = True
        return []

    def close(self):
        self.connected = False


def _offer(*, format="", formats=(), preferred=""):
    return AcquisitionOffer(
        offer_id="offer",
        format=format,
        formats=tuple(formats),
        preferred_format=preferred,
    )


def test_legacy_offer_constructor_fields_remain_positional_compatible():
    offer = AcquisitionOffer(
        "legacy", "ebook", "epub", "none", "http", {"url": "https://example.test/book"}, 7, {"x": 1}
    )

    assert offer.offer_id == "legacy"
    assert offer.format == "epub"
    assert offer.drm == "none"
    assert offer.priority == 7
    assert offer.formats == ()
    assert offer.preferred_format == ""


def test_offer_formats_merge_legacy_and_multiple_attested_formats_without_notice_codes():
    offer = _offer(
        format="application/epub+zip",
        formats=("PDF", "kepub", "MONE", "epub", "EXTL"),
    )

    assert acquisition_offer_formats(offer) == ("epub", "pdf", "kepub")


def test_preferred_format_must_be_attested_and_is_never_invented():
    valid = _offer(formats=("epub", "pdf"), preferred="PDF")
    invalid = _offer(formats=("epub",), preferred="pdf")
    absent = _offer(formats=("epub", "pdf"))

    assert acquisition_offer_preferred_format(valid) == "pdf"
    assert acquisition_offer_preferred_format(invalid) == ""
    assert acquisition_offer_preferred_format(absent) == ""


def test_offer_metadata_aggregates_alternatives_without_arbitrary_preference():
    offers = [
        _offer(formats=("epub", "pdf"), preferred="epub"),
        _offer(format="kepub", formats=("pdf",), preferred="pdf"),
    ]

    metadata = acquisition_offers_metadata(offers)

    assert metadata["available_formats"] == ["epub", "pdf", "kepub"]
    assert metadata["format_source"] == "acquisition_offers"
    assert "preferred_format" not in metadata


def test_single_consistent_preference_is_projected():
    offers = [
        _offer(formats=("epub", "pdf"), preferred="epub"),
        _offer(format="epub", preferred="epub"),
    ]

    assert acquisition_offers_metadata(offers)["preferred_format"] == "epub"


def test_provider_session_contract_has_empty_offer_fallback():
    session = Session(
        descriptor=ProviderDescriptor(id="test", display_name="Test"),
        credential_id="cred",
        label="Test",
        credentials={},
        options={},
    )

    assert session.acquisition_offers(object()) == []


def test_core_projection_preserves_unknown_primary_format_and_adds_filterable_alternatives():
    row = {
        "title": "Livre",
        "media_format": "",
        "format": "MONO",
        "metadata": {"format_source": "unknown"},
    }
    projected = project_acquisition_offer_formats(
        row,
        [_offer(formats=("epub", "pdf"), preferred="epub")],
    )

    assert projected["media_format"] == ""
    assert projected["format"] == "MONO"
    assert projected["metadata"]["available_formats"] == ["epub", "pdf"]
    assert projected["metadata"]["preferred_format"] == "epub"
    assert projected["metadata"]["offer_format_source"] == "acquisition_offers"
    assert available_formats(projected) == ("epub", "pdf")


def test_epub_family_and_pdf_filters_use_only_attested_projected_formats():
    projected = project_acquisition_offer_formats(
        {"title": "Livre", "metadata": {}},
        [_offer(formats=("kepub", "pdf", "MONE"))],
    )

    assert filter_loaded_results([projected], content_types=["epub"]) == [projected]
    assert filter_loaded_results([projected], content_types=["pdf"]) == [projected]
    assert filter_loaded_results([projected], content_types=["mp3"]) == []


def test_projection_does_not_mutate_input_metadata():
    row = {"title": "Livre", "metadata": {"available_formats": ["epub"]}}
    original_metadata = dict(row["metadata"])

    projected = project_acquisition_offer_formats(row, [_offer(formats=("pdf",))])

    assert row["metadata"] == original_metadata
    assert projected["metadata"]["available_formats"] == ["epub", "pdf"]
