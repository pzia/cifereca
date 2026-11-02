"""P1 #4276: Paris catalogue medium and bibliographic kind are independent.

The observed Resource.Type / Frmt / RscBase combinations are synthesized,
NOT copied from the account capture. No Paris client session, network, account,
filesystem, loan or upstream mutation is involved. Strict xfails are explicit
P1 implementation acceptance gates while P0 #3723 owns the Paris provider.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca_paris_client import ParisClient
from cifereca_paris_normalize import normalize_search_result


# Seven *observed combinations*, invented IDs and titles.
OBSERVED = (
    ("Livre", "MONO", "SYRACUSE", "physical", "book"),
    ("BD", "BDTP", "SYRACUSE", "physical", "comics"),
    ("Musique", "AUDI", "SYRACUSE", "physical", "music"),
    ("Film", "AUVI", "SYRACUSE", "physical", "video"),
    ("Revue", "PERI", "SYRACUSE", "physical", "periodical"),
    ("Livre numérique", "MONE", "DILICOM", "digital", "ebook"),
    ("Livre numérique", "MONE", "STORYPLAYR", "digital", "ebook"),
)


def record(kind, frmt, base, *, scenario="CATALOGUE", record_id="fixture"):
    return normalize_search_result({
        "Resource": {
            "RscId": record_id, "RscBase": base,
            "Type": kind, "Frmt": frmt, "Ttl": "A synthetic title",
        }
    }, scenario)


def client_with_rows(by_scenario):
    """Exercise *real* ParisClient.search without constructing an HTTP client."""
    client = object.__new__(ParisClient)
    client.diagnostics = SimpleNamespace(add=lambda *_args, **_kwargs: None)
    client._timed = lambda _category, _operation, action: action()
    client._search_scenario = (
        lambda _query, scenario, _page, _size: by_scenario.get(scenario, [])
    )
    client._last_scenario_facet_observation = None
    client.last_search_warnings = []
    client.last_search_response_facets = []
    return client


@pytest.mark.parametrize(("kind", "frmt", "base", "medium", "_category"), OBSERVED)
def test_observed_bibliographic_codes_are_not_claimed_as_files(
    kind, frmt, base, medium, _category,
):
    item = record(kind, frmt, base)
    assert item is not None
    assert item.medium == medium
    assert item.type == kind
    assert item.format is None
    assert item.metadata["available_formats"] == []
    assert item.metadata["format_evidence"] == []
    assert item.metadata["paris_bibliographic_format"] == frmt
    assert item.metadata["format_source"] == "unknown"
    # Type and Frmt alone never prove download, loan, acquisition or quota.
    assert item.download_url is None
    assert item.downloadable is None
    assert item.owned is None


@pytest.mark.parametrize(("kind", "frmt", "base", "_medium", "category"), OBSERVED)
@pytest.mark.xfail(strict=True, reason=(
    "#4276 pending Paris P0 #3723: a separate typed document kind must "
    "survive normalization without equating all physical media with books"
))
def test_pending_separate_document_kind_per_observed_type(
    kind, frmt, base, _medium, category,
):
    item = record(kind, frmt, base)
    assert item is not None
    # Contract to negotiate with Core, without inventing a media codec.
    assert item.metadata.get("document_kind") == category
    if category in {"comics", "music", "video", "periodical"}:
        assert item.content_type != "physical"


@pytest.mark.xfail(strict=True, reason=(
    "#4276 pending Paris P0 #3723: CATALOGUE can include DILICOM ebooks"
))
def test_pending_physical_scope_filters_mixed_catalogue_by_item_evidence():
    rows = [
        record("Livre", "MONO", "SYRACUSE", record_id="paper"),
        record("Film", "AUVI", "SYRACUSE", record_id="dvd"),
        record("Livre numérique", "MONE", "DILICOM", record_id="ebook"),
        record("Livre numérique", "MONE", "STORYPLAYR", record_id="story"),
    ]
    client = client_with_rows({"CATALOGUE": rows})
    result = client.search("synthetic", scope="physical")
    assert [item.record_id for item in result] == ["paper", "dvd"]
    assert all(item.medium == "physical" for item in result)


@pytest.mark.xfail(strict=True, reason=(
    "#4276 pending Paris P0 #3723: EBOOKS provenance cannot override "
    "positively physical Syracuse film evidence"
))
def test_pending_ebooks_scenario_cannot_convert_physical_film_to_ebook():
    film = record("Film", "AUVI", "SYRACUSE", scenario="EBOOKS")
    assert film is not None
    assert film.medium == "physical"
    assert film.metadata.get("document_kind") == "video"
    assert film.format is None


@pytest.mark.xfail(strict=True, reason=(
    "#4276 pending Paris P0 #3723: unknown support must not silently "
    "become a physical book"
))
def test_pending_unknown_type_and_base_remain_unknown_not_paper():
    item = record("Unrecognized media", "UNSUPPORTED", "OTHER", scenario="EBOOKS")
    assert item is not None
    assert item.medium == "unknown"
    assert item.metadata.get("document_kind", "unknown") == "unknown"
    assert item.format is None


@pytest.mark.xfail(strict=True, reason=(
    "#4276 pending Paris P0 #3723: duplicate result must retain both "
    "catalogue provenances rather than discarding the EBOOKS sighting"
))
def test_pending_duplicate_record_keeps_both_search_provenances():
    c = record("Livre numérique", "MONE", "DILICOM", scenario="CATALOGUE")
    e = record("Livre numérique", "MONE", "DILICOM", scenario="EBOOKS")
    client = client_with_rows({"CATALOGUE": [c], "EBOOKS": [e]})
    results = client.search("synthetic", scope="all")
    assert len(results) == 1
    assert set(results[0].metadata.get("search_scenarios", ())) == {
        "CATALOGUE", "EBOOKS",
    }


def test_catalogue_evidence_does_not_authorize_digital_pnb_for_storyplayr():
    item = record("Livre numérique", "MONE", "STORYPLAYR")
    assert item is not None
    assert item.medium == "digital"
    assert item.docbase == "STORYPLAYR"  # never claim DILICOM from medium
    assert item.format is None
    assert item.download_url is None
    assert item.owned is None
    assert item.downloadable is None


@pytest.mark.parametrize("code", ["MONO", "BDTP", "AUDI", "AUVI", "PERI", "MONE"])
def test_catalogue_bibliographic_frmt_never_turns_into_a_download_codec(code):
    item = record("Unrecognized media", code, "SYRACUSE")
    assert item is not None
    assert item.format is None
    assert item.metadata["available_formats"] == []
    assert item.metadata["paris_bibliographic_format"] == code


def test_real_explicit_epub_evidence_is_not_erased_by_bibliographic_code():
    item = normalize_search_result({
        "Resource": {
            "RscId": "synthetic", "RscBase": "DILICOM", "Type": "Livre numérique",
            "Frmt": "MONE", "MediaFormat": "application/epub+zip",
        }
    }, "CATALOGUE")
    assert item is not None
    assert item.medium == "digital"
    assert item.format == "EPUB"
    assert item.metadata["available_formats"] == ["EPUB"]
    assert "Resource.MediaFormat" in item.metadata["format_evidence"]
    assert item.metadata["paris_bibliographic_format"] == "MONE"
