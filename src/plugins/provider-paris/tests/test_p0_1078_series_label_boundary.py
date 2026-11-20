"""A rejected series label cannot be emitted or hidden by the other fragment."""
import pytest

from cifereca_paris_bibliography import (
    project_attested_search_series, public_search_series_metadata,
)


GOOD = '<a href="/Search?field=SeriesTitle_idx">Cycle astral</a>'
PROOF = {
    "source": "paris.syracuse.search", "source_field": "Search.CustomResult",
    "scope": "EBOOKS", "confidence": "linked_search_relation",
    "freshness": "search_response",
}


@pytest.mark.parametrize("label", [
    "Cycle\u202eastral", "Cycle\u2066astral", "Cycle\u200bastral",
    "Cycle\x1castral", "Cycle&#x202e;astral", "Cycle\ud800astral",
    "/home/private/series", "~/private/series", "C:/private/series",
    "file:private", "Cycle\\private", "api_key=hidden",
    "Authorization: private", "signature=hidden", "refresh_token=hidden",
])
def test_unsafe_series_is_rejected_in_each_projection(label):
    raw = '<a href="/Search?field=SeriesTitle_idx">' + label + '</a>'
    assert project_attested_search_series({"CustomResult": raw}, "EBOOKS") == {}
    assert project_attested_search_series(
        {"CustomResult": raw, "CompactResult": GOOD}, "EBOOKS",
    ) == {}
    metadata = {"series": [label], "bibliography_evidence": {"series": PROOF}}
    assert public_search_series_metadata(metadata, "EBOOKS") == {}


def test_normal_series_whitespace_and_proof_survive_both_steps():
    result = project_attested_search_series(
        {"CustomResult": '<a href="/Search?field=SeriesTitle_idx"> Cycle\n  astral &amp; suites </a>'},
        "EBOOKS",
    )
    assert result == {
        "series": ["Cycle astral & suites"],
        "bibliography_evidence": {"series": PROOF},
    }
    assert public_search_series_metadata(result, "EBOOKS") == result


def test_preserve_distinct_existing_source_and_public_length_bounds():
    raw = '<a href="/Search?field=SeriesTitle_idx">' + "A" * 200 + '</a>'
    result = project_attested_search_series({"CustomResult": raw}, "EBOOKS")
    assert result["series"] == ["A" * 200]
    assert public_search_series_metadata(result, "EBOOKS") == {}
