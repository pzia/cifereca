"""#1078: a Syracuse series link must target the attested Search route.

Synthetic per-result HTML only. No live Paris schema, network call, right,
edition join, GetRecord or provider activation is claimed here.
"""
from __future__ import annotations

import pytest

from cifereca_paris_bibliography import project_attested_search_series
from cifereca_paris_normalize import normalize_search_result


GOOD = '<a href="/Search?field=SeriesTitle_idx">Cycle des astres</a>'


def _row(fragment: str):
    return normalize_search_result({
        "Resource": {
            "RscId": "book-exact", "RscBase": "DILICOM",
            "Ttl": "Titre synthétique", "Subj": ["Astronomie"],
        },
        "CustomResult": fragment,
    }, "EBOOKS")


@pytest.mark.parametrize("href", (
    "/download?field=SeriesTitle_idx",
    "/Record?field=SeriesTitle_exact",
    "/Search/Detail?field=SeriesTitle_idx",
    "/redirect?query=SeriesTitle_idx%3ASaga",
    "../other?field=SeriesTitle_idx",
    "https://bibliotheques.paris.fr/other?field=SeriesTitle_idx",
    "/%53earch?field=SeriesTitle_idx",
    "/Search%2FDetail?field=SeriesTitle_idx",
))
def test_unqualified_route_cannot_attest_series_even_with_valid_selector(href):
    bad = '<a href="' + href.replace("&", "&amp;") + '">Fausse série</a>'
    assert project_attested_search_series({"CustomResult": bad}, "EBOOKS") == {}
    # One bad fragment cannot be suppressed by a second valid fragment.
    assert project_attested_search_series(
        {"CustomResult": bad, "CompactResult": GOOD}, "EBOOKS",
    ) == {}
    item = _row(bad)
    assert item is not None
    assert "series" not in item.metadata
    assert "series" not in item.metadata.get("bibliography_evidence", {})
    assert item.metadata["subjects"] == ["Astronomie"]


@pytest.mark.parametrize("href", (
    "/Search?field=SeriesTitle_idx",
    "/search.aspx?query=SeriesTitle_exact%3ACycle",
    "./Search?field=SeriesTitle_idx",
    "../Search?field=SeriesTitle_idx",
    "https://bibliotheques.paris.fr/Search?field=SeriesTitle_idx",
    "https://bibliotheques.paris.fr/search.aspx?field=SeriesTitle_idx",
))
def test_exact_qualified_search_route_still_attests_result_owned_series(href):
    fragment = '<a href="' + href.replace("&", "&amp;") + '">Cycle des astres</a>'
    item = _row(fragment)
    assert item is not None
    assert item.metadata["series"] == ["Cycle des astres"]
    assert item.metadata["bibliography_evidence"]["series"] == {
        "source": "paris.syracuse.search",
        "source_field": "Search.CustomResult",
        "scope": "EBOOKS",
        "confidence": "linked_search_relation",
        "freshness": "search_response",
    }

@pytest.mark.parametrize("query", (
    "Author_idx%3AOther",
    "SeriesTitle_exact%3ACycle",
    "Subject_exact%3ACycle",
    "SeriesTitle_idx%3A",
    "Author_exact%3ASecret%20name",
))
def test_conflicting_explicit_query_index_rejects_series_evidence(query):
    href = "/Search?field=SeriesTitle_idx&amp;query=" + query
    bad = '<a href="' + href + '">Cycle des astres</a>'
    assert project_attested_search_series({"CustomResult": bad}, "EBOOKS") == {}
    assert project_attested_search_series({
        "CustomResult": bad, "CompactResult": GOOD,
    }, "EBOOKS") == {}


@pytest.mark.parametrize("query", (
    "plain%20title",
    "Cycle%20A%3A%20tome%201",
    "SeriesTitle_idx%3ACycle",
    "seriesTITLE_IDX%3ACycle",
))
def test_compatible_explicit_query_or_free_text_remains_attested(query):
    href = "/Search?field=SeriesTitle_idx&amp;query=" + query
    row = _row('<a href="' + href + '">Cycle des astres</a>')
    assert row is not None
    assert row.metadata["series"] == ["Cycle des astres"]
