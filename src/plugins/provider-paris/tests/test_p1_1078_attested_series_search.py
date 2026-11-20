"""P1 #1078: only result-owned Syracuse Search anchors can attest a series.

The snippets below are synthetic approximations of the observed individual
Search.CustomResult/CompactResult link class; they do not pretend to prove
an upstream schema or to exercise GetRecord. No network or loan I/O.
"""
from __future__ import annotations

import pytest

from cifereca_paris_bibliography import project_attested_search_series
from cifereca_paris_normalize import normalize_search_result


def _record(scenario, **fields):
    return normalize_search_result({
        "Resource": {
            "RscId": "exact-edition", "RscBase": (
                "DILICOM" if scenario == "EBOOKS" else "SYRACUSE"
            ), "Ttl": "Synthetic title", "Subj": ["Nature"],
        },
        **fields,
    }, scenario)


SERIES_LINK = (
    '<div class="notices"><a href="/Search?query=SeriesTitle_idx%3ASaga">'
    "<span>La saga &amp; ses suites</span></a></div>"
)


@pytest.mark.parametrize("scenario", ["CATALOGUE", "EBOOKS"])
@pytest.mark.parametrize("field", ["CustomResult", "CompactResult"])
def test_individual_series_link_is_projected_with_exact_html_provenance(
    scenario, field,
):
    item = _record(scenario, **{field: SERIES_LINK})
    assert item is not None
    assert item.metadata["series"] == ["La saga & ses suites"]
    assert item.metadata["subjects"] == ["Nature"]
    evidence = item.metadata["bibliography_evidence"]
    assert evidence["subjects"]["source_field"] == "Resource.Subj"
    assert evidence["series"] == {
        "source": "paris.syracuse.search",
        "source_field": "Search." + field,
        "scope": scenario,
        "confidence": "linked_search_relation",
        "freshness": "search_response",
    }
    assert "audience" not in item.metadata
    assert "age_min" not in item.metadata
    assert "owned" not in item.metadata



@pytest.mark.parametrize("field_name", [
    "SeriesTitle_exact", "SeriesTitle_idx",
    "seriestitle_exact", "SERIESTITLE_IDX",
])
@pytest.mark.parametrize("scenario", ["CATALOGUE", "EBOOKS"])
def test_both_observed_series_query_fields_are_scoped_to_exact_result(
    field_name, scenario,
):
    # These are Search-provided per-result links, not the global facet list.
    item = _record(scenario, CustomResult=(
        '<a href="/Search?field=' + field_name + '&amp;query=example">'
        'Cycle astral</a>'
    ))
    assert item.metadata["series"] == ["Cycle astral"]
    assert (item.metadata["bibliography_evidence"]["series"]["source_field"]
            == "Search.CustomResult")
    assert "audience" not in item.metadata


@pytest.mark.parametrize("field_name", [
    "SeriesTitle_exactly", "SeriesTitle_idx2", "NotSeriesTitle_exact",
    "SeriesTitle_exact_other", "SeriesTitle_idx_sub",
])
def test_similar_unobserved_search_fields_never_mint_series(field_name):
    item = _record("CATALOGUE", CustomResult=(
        '<a href="/Search?field=' + field_name + '">Not a series</a>'
    ))
    assert "series" not in item.metadata


@pytest.mark.parametrize("href", (
    "/SeriesTitle_idx",
    "/Search?label=SeriesTitle_idx",
    "/Search?field=Author_idx&query=Texte%20SeriesTitle_exact",
    "/Search?query=Texte%20SeriesTitle_idx%3ASaga",
    "/Search?field=Author_idx&field=SeriesTitle_idx",
    "/Search?label=Texte%26field%3DSeriesTitle_idx",
    "/Search?query=SeriesTitle_idx%3A",
))
def test_series_requires_an_unambiguous_search_selector(href):
    item = _record("CATALOGUE", CustomResult=(
        '<a href="' + href.replace("&", "&amp;") + '">Libellé visible</a>'
    ))
    assert "series" not in item.metadata
    assert "series" not in item.metadata["bibliography_evidence"]
    assert item.metadata["subjects"] == ["Nature"]


def test_qualified_query_in_search_aspx_preserves_the_per_notice_series():
    item = _record("EBOOKS", CompactResult=(
        '<a href="/search.aspx?SC=EBOOKS&amp;QUERY=SeriesTitle_exact%3ACycle">'
        'Cycle des astres</a>'
    ))
    assert item.metadata["series"] == ["Cycle des astres"]
    assert item.metadata["bibliography_evidence"]["series"]["scope"] == "EBOOKS"



def test_same_link_in_both_display_fields_is_one_relation_not_two():
    item = _record("CATALOGUE", CustomResult=SERIES_LINK, CompactResult=SERIES_LINK)
    assert item.metadata["series"] == ["La saga & ses suites"]
    assert (item.metadata["bibliography_evidence"]["series"]["source_field"]
            == "Search.CustomResult")


def test_conflicting_per_result_series_are_unknown():
    conflicting = SERIES_LINK.replace("La saga &amp; ses suites", "Autre série")
    item = _record(
        "CATALOGUE", CustomResult=SERIES_LINK, CompactResult=conflicting,
    )
    assert "series" not in item.metadata
    assert item.metadata["subjects"] == ["Nature"]
    assert "series" not in item.metadata["bibliography_evidence"]


@pytest.mark.parametrize("snippet", [
    '<a href="/Search?field=SeriesTitle_idx">A</a><a '
    'href="/Search?field=SeriesTitle_idx">B</a>',
    '<a href="https://evil.invalid/Search?field=SeriesTitle_idx">Secret</a>',
    '<a href="http://bibliotheques.paris.fr/Search?field=SeriesTitle_idx">X</a>',
    '<a href="javascript:alert(SeriesTitle_idx)">X</a>',
    '<a href="https://user:password@bibliotheques.paris.fr/Search?field=SeriesTitle_idx">X</a>',
    '<a href="/Search?field=SeriesTitle_idx_other">X</a>',
    '<a href="/Search?field=SeriesTitle_idx"><script>alert(1)</script></a>',
    '<a href="/Search?field=SeriesTitle_idx">PRIVATE@EMAIL.invalid</a>',
    '<a href="/Search?field=SeriesTitle_idx">https://private.invalid/token</a>',
    '<a href="/Search?field=SeriesTitle_idx">Unclosed',
])
def test_untrusted_or_ambiguous_anchor_cannot_become_metadata(snippet):
    item = _record("CATALOGUE", CustomResult=snippet)
    assert "series" not in item.metadata
    assert "series" not in item.metadata["bibliography_evidence"]


def test_global_facets_and_resource_guess_are_not_individual_series():
    raw = {
        "Resource": {"RscId": "a", "RscBase": "SYRACUSE",
                     "Ttl": "Example", "SeriesTitle": "unverified"},
        "FacetCollectionList": [{
            "FacetField": "SeriesTitle_exact",
            "FacetList": [{"Label": "False positive", "Count": 1}],
        }],
        "CustomResult": '<a href="/Search?field=Audience_exact">Youth</a>',
    }
    item = normalize_search_result(raw, "CATALOGUE")
    assert item is not None
    assert "series" not in item.metadata
    assert "audience" not in item.metadata
    assert project_attested_search_series(raw, "UNKNOWN") == {}


def test_malformed_or_oversized_result_cannot_leak_payload():
    private = "https://bad.invalid/?token=PRIVATE_SECRET"
    for raw in ({"CustomResult": private}, {"CustomResult": "X" * 32769},
                {"CustomResult": {"Value": private}},
                {"CustomResult": SERIES_LINK * 100}):
        item = _record("EBOOKS", **raw)
        assert "series" not in item.metadata
        assert private not in repr(item.metadata)
    assert project_attested_search_series(
        {"CustomResult": '<a href="/Search?field=SeriesTitle_idx">A</a>'},
        "UNKNOWN",
    ) == {}


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
@pytest.mark.parametrize("other", ("CustomResult", "CompactResult"))
@pytest.mark.parametrize("bad", (
    '<a href="/Search?field=SeriesTitle_idx">A</a><a href="/Search?field=SeriesTitle_exact">B</a>',
    '<a href="/Search?field=SeriesTitle_idx">Unclosed',
    '<a href="/Search?field=SeriesTitle_idx"><script>hidden()</script></a>',
    '<template><a href="/Search?field=SeriesTitle_idx">Hidden</a></template>',
    '<a href="https://bibliotheques.paris.fr:bad/Search?field=SeriesTitle_idx">Bad port</a>',
    '<a href="/Search?field=SeriesTitle_idx%00">Control</a>',
    '<a href="/%5c%5coutside.invalid/Search?field=SeriesTitle_idx">Separator</a>',
    '<a href="/Search?field=SeriesTitle_idx%23fragment">Fragment</a>',
    '<a href="/Search?field=SeriesTitle_idx" href="/anything">Duplicate href</a>',
    '<a href="/Search?field=SeriesTitle_idx">PRIVATE@EMAIL.invalid</a>',
    '<a href="/Search?field=SeriesTitle_idx">' + "A"*1025 + '</a>',
    SERIES_LINK * 65,
))
def test_bad_or_conflicting_fragment_cannot_be_hidden_by_other_valid_fragment(scenario, other, bad):
    good = "CompactResult" if other == "CustomResult" else "CustomResult"
    item = _record(scenario, **{good: SERIES_LINK, other: bad})
    assert item is not None
    assert "series" not in item.metadata
    assert item.metadata["subjects"] == ["Nature"]
    assert "series" not in item.metadata["bibliography_evidence"]
    assert "PRIVATE@" not in repr(item.metadata)


@pytest.mark.parametrize("unrelated", (None, "", '<a href="/Search?field=Author_idx">Writer</a>',
                                      '<div>Plain bibliographic text</div>'))
def test_absent_series_in_second_fragment_does_not_cancel_valid_source(unrelated):
    item = _record("EBOOKS", CustomResult=unrelated, CompactResult=SERIES_LINK)
    assert item.metadata["series"] == ["La saga & ses suites"]
    assert item.metadata["bibliography_evidence"]["series"]["source_field"] == "Search.CompactResult"


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
@pytest.mark.parametrize("fragment", (
    '<template><template></template>' + SERIES_LINK + '</template>',
    '<template></unrelated>' + SERIES_LINK + '</template>',
    '<noscript><template></template>' + SERIES_LINK + '</noscript>',
    '<div hidden>' + SERIES_LINK + '</div>',
    '<div aria-hidden="true" aria-hidden="false">' + SERIES_LINK + '</div>',
    '<div style="display : none !important">' + SERIES_LINK + '</div>',
    '<a hidden href="/Search?field=SeriesTitle_idx">Invisible</a>',
    '<a href="/Search?field=SeriesTitle_idx"><span hidden>Hidden</span>Visible</a>',
    '<iframe>' + SERIES_LINK + '</iframe>',
    '<svg>' + SERIES_LINK + '</svg>',
    '<div>' * 257 + SERIES_LINK + '</div>' * 257,
))
def test_hidden_or_overdeep_series_does_not_attest_result(scenario, fragment):
    row = _record(scenario, CustomResult=fragment)
    assert "series" not in row.metadata
    assert "series" not in row.metadata["bibliography_evidence"]
    row = _record(scenario, CustomResult=fragment, CompactResult=SERIES_LINK)
    assert "series" not in row.metadata


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
def test_closed_inert_subtree_and_void_elements_do_not_hide_visible_series(scenario):
    row = _record(scenario, CustomResult=(
        '<template><template><img src="ignored"></template></template>'
        '<div hidden><br><span>Not a series</span></div>' + SERIES_LINK))
    assert row.metadata["series"] == ["La saga & ses suites"]


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
@pytest.mark.parametrize("fragment", (
    '<title>' + SERIES_LINK + '</title>',
    '<xmp>' + SERIES_LINK + '</xmp>',
    '<noembed>' + SERIES_LINK + '</noembed>',
    '<noframes>' + SERIES_LINK + '</noframes>',
    '<textarea>' + SERIES_LINK + '</textarea>',
    '<script>' + SERIES_LINK + '</script>',
    '<style>' + SERIES_LINK + '</style>',
    '<div hidden><textarea></div></textarea>' + SERIES_LINK + '</div>',
    '<div hidden/><span>' + SERIES_LINK + '</span>',
    '<template/>' + SERIES_LINK,
    '<plaintext></plaintext>' + SERIES_LINK,
    '<textarea>unclosed',
))
def test_raw_text_and_malformed_inert_boundaries_cannot_attest_series(scenario, fragment):
    # Synthetic HTML boundaries, not an additional Syracuse field/endpoint.
    for fields in ({"CustomResult": fragment},
                   {"CustomResult": fragment, "CompactResult": SERIES_LINK}):
        row = _record(scenario, **fields)
        assert "series" not in row.metadata
        assert "series" not in row.metadata["bibliography_evidence"]
        assert row.metadata["subjects"] == ["Nature"]


@pytest.mark.parametrize("tag", ("title", "xmp", "noembed", "textarea", "noscript"))
def test_closed_unrelated_raw_text_does_not_suppress_visible_relation(tag):
    fragment = f"<{tag}>Unrelated text</{tag}>" + SERIES_LINK
    row = _record("CATALOGUE", CustomResult=fragment)
    assert row.metadata["series"] == ["La saga & ses suites"]
