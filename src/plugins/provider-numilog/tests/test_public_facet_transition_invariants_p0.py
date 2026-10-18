"""P0 #1886: public facet URLs never silently change unrelated filters.

Offline, synthetic links embedded beside a *sanitized observed* Numilog card.
No remote access, cookies, account, signed URL or guess of search-by-author.
"""
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import pytest

from cifereca_numilog_public import (
    GRID_ID, SEARCH_PATH, PublicSearchParseError, parse_public_search,
)

FIXTURES = Path(__file__).parent / "fixtures"
HTML = (FIXTURES / "public_search_jefferson_20261016.html").read_text()
START = HTML.index('<tr class="LivreDuThemeDetail">')
CARD = HTML[START:HTML.index('<tr class="LivreDuThemeDetail">', START + 1)]
BASE = 'https://www.numilog.com' + SEARCH_PATH
PARAMS = {'mode': 'brSimple', 'query': 'jefferson'}


def url(**parameters):
    return BASE + '?' + urlencode({**PARAMS, **parameters})


def page(*, source, facet, total=1):
    """Emit a qualified first card, one vendor-like facet, no pagination."""
    html = (
        f'<span class="ResultatDetail">{total}</span>'
        f'<table id="{GRID_ID}"><tbody>{CARD}</tbody></table>'
        f'<nav><a class="panel-sous-filtre" href="{escape(facet, quote=True)}">'
        'Autre filtre (4)</a></nav>'
    )
    return parse_public_search(html, source)


def test_observed_unfiltered_facet_still_accepts_one_active_dimension():
    result = page(source=url(), facet=url(fltrLang="fr"))
    assert result.total_results == 1
    assert len(result.items) == 1
    assert result.facets[0]["parameter"] == "fltrLang"
    assert result.facets[0]["value"] == "fr"
    assert result.facets[0]["url"] == url(fltrLang="fr")
    assert result.items[0].owned is False
    assert result.items[0].downloadable is False
    assert result.items[0].download_url is None


def test_new_facet_preserves_previously_selected_language_and_theme():
    source = url(fltrLang="fr", fltrTheme="500128")
    target = url(fltrLang="fr", fltrTheme="500128", fltrPrice="5")
    result = page(source=source, facet=target)
    assert len(result.facets) == 1
    assert result.facets[0]["parameter"] == "fltrPrice"
    assert result.facets[0]["value"] == "5"


def test_switching_same_facet_dimension_keeps_other_selected_dimensions():
    source = url(fltrLang="fr", fltrPrice="5")
    target = url(fltrLang="english", fltrPrice="5")
    assert page(source=source, facet=target).facets[0]["parameter"] == "fltrLang"


@pytest.mark.parametrize(("selected", "target"), [
    ({"fltrLang": "fr"}, {"fltrPrice": "5"}),
    ({"fltrTheme": "500128"}, {"fltrLang": "fr"}),
    ({"fltrPub": "12-21"}, {"fltrLang": "fr"}),
    ({"fltrLang": "fr", "fltrTheme": "500128"},
     {"fltrTheme": "500128", "fltrPrice": "5"}),
    ({"fltrLang": "fr", "fltrPrice": "5"},
     {"fltrLang": "fr", "fltrPub": "12-21"}),
    ({"fltrLang": "fr", "fltrPrice": "5"},
     {"fltrLang": "english", "fltrPrice": "10"}),
])
def test_dropped_or_multiple_changed_filters_rejected(selected, target):
    with pytest.raises(PublicSearchParseError, match="invalid_facets") as exc:
        page(source=url(**selected), facet=url(**target))
    assert "jefferson" not in str(exc.value)


@pytest.mark.parametrize("page_index", ["1", "2", "99", "100"])
def test_facet_link_cannot_jump_to_an_unobserved_search_page(page_index):
    with pytest.raises(PublicSearchParseError, match="invalid_facets"):
        page(source=url(fltrLang="fr"),
             facet=url(fltrLang="fr", fltrPrice="5", GRID_PAGE=page_index))


def test_page_one_can_offer_a_filter_reset_to_page_zero():
    result = page(source=url(fltrLang="fr", GRID_PAGE="1"),
                  facet=url(fltrLang="fr", fltrPrice="5"))
    assert result.page_index == 1
    assert result.facets[0]["parameter"] == "fltrPrice"


def test_explicit_page_zero_for_a_facet_is_equivalent_to_default_first_page():
    result = page(source=url(fltrLang="fr"),
                  facet=url(fltrLang="fr", fltrPrice="5", GRID_PAGE="0"))
    assert result.facets[0]["parameter"] == "fltrPrice"


def test_clearing_an_active_facet_is_unqualified_until_observed_upstream():
    # Removing a filter is NOT the same contract as applying a new one.
    with pytest.raises(PublicSearchParseError, match="invalid_facets"):
        page(source=url(fltrLang="fr", fltrPrice="5"),
             facet=url(fltrPrice="5"))


def test_invalid_facet_does_not_fall_back_to_unfiltered_results():
    with pytest.raises(PublicSearchParseError, match="invalid_facets"):
        page(source=url(fltrLang="fr"),
             facet=url(fltrPrice="5"), total=1)
