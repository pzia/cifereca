from __future__ import annotations

from cifereca.plugin_api import SearchResult, acquisition_format_projection
from cifereca_paris import _paris_acquisition_offer_set, _project_paris_acquisition_formats


def item(*, formats, evidence=("Resource.Format",), display_format=None):
    return SearchResult(
        id="paris:1",
        record_id="1",
        docbase="DILICOM",
        medium="digital",
        title="Livre",
        author="Auteur",
        type="Livre numérique",
        format=display_format,
        publisher=None,
        published_at=None,
        description=None,
        thumbnail_url=None,
        friendly_url=None,
        scenario="EBOOKS",
        content_type="ebook",
        metadata={
            "available_formats": list(formats),
            "format_evidence": list(evidence),
            "paris_bibliographic_format": "MONE",
        },
    )


def test_multiformat_paris_offer_is_attested_without_inventing_preference():
    row = item(formats=("EPUB", "PDF"))
    choices = _paris_acquisition_offer_set(row)

    assert len(choices.offers) == 1
    assert choices.offers[0].formats == ("epub", "pdf")
    assert choices.offers[0].format == ""
    assert choices.offers[0].drm == "unknown"
    assert choices.preferences.format_order == ()
    assert acquisition_format_projection(choices)["available_formats"] == ("epub", "pdf")


def test_bibliographic_codes_never_become_file_formats():
    row = item(formats=("MONE", "MONO", "EXTL"))
    choices = _paris_acquisition_offer_set(row)

    assert choices.offers == ()
    projected = _project_paris_acquisition_formats(row)
    assert projected.metadata["available_formats"] == ()
    assert projected.metadata["format_evidence"] == "unknown"


def test_single_explicit_format_is_projected_for_facets():
    row = item(formats=("application/epub+zip",))
    projected = _project_paris_acquisition_formats(row)

    assert projected.metadata["available_formats"] == ("epub",)
    assert projected.metadata["format_evidence"] == "acquisition_offers"
    assert projected.metadata["paris_format_evidence"] == ["Resource.Format"]
