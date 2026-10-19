"""Numilog #1886: hidden HTML cannot establish public catalogue evidence."""
from pathlib import Path

import pytest

from cifereca_numilog_public import GRID_ID, PublicSearchParseError, parse_public_search

HTML = (Path(__file__).with_name("fixtures") / "public_search_jefferson_20261016.html").read_text()
URL = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"


def test_hidden_template_with_competing_grid_and_total_is_ignored():
    before = parse_public_search(HTML, URL)
    fake = (f'<template><span class="ResultatDetail">1</span>'
            f'<table id="{GRID_ID}"><tr class="LivreDuThemeDetail">'
            '<a class="TitleBook">PRIVATE_SENTINEL</a></tr></table></template>')
    new = HTML.replace(f'<table id="{GRID_ID}">', fake + f'<table id="{GRID_ID}">', 1)
    assert new != HTML
    after = parse_public_search(new, URL)
    assert len(after.items) == 20
    assert after.total_results == before.total_results
    assert [x.record_id for x in after.items] == [x.record_id for x in before.items]
    assert "PRIVATE_SENTINEL" not in repr(after)


def test_hidden_author_is_not_projected_into_public_fields():
    fake = '<a class="Author" aria-hidden="true">PRIVATE_SENTINEL</a>'
    new = HTML.replace('<a class="Author"', fake + '<a class="Author"', 1)
    assert new != HTML
    after = parse_public_search(new, URL)
    assert after.items[0].metadata["authors"] == parse_public_search(HTML, URL).items[0].metadata["authors"]
    assert "PRIVATE_SENTINEL" not in repr(after)


@pytest.mark.parametrize("hidden", [
    "<template>PRIVATE_SENTINEL</template>",
    '<span hidden>PRIVATE_SENTINEL</span>',
    '<span aria-hidden="true">PRIVATE_SENTINEL</span>',
    '<span style="DISPLAY : NONE">PRIVATE_SENTINEL</span>',
])
def test_hidden_descendant_in_title_fails_closed(hidden):
    new = HTML.replace(
        ">Jefferson prend des risques</a>",
        ">" + hidden + "Jefferson prend des risques</a>", 1,
    )
    assert new != HTML
    with pytest.raises(PublicSearchParseError, match="hidden_field") as error:
        parse_public_search(new, URL)
    assert "PRIVATE_SENTINEL" not in str(error.value)
