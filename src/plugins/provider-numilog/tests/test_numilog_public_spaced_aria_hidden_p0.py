"""#1886: aria-hidden with whitespace is not an author/title source.

Uses the stored Numilog public search fixture; no requests or accounts.
"""
from pathlib import Path

import pytest

from cifereca_numilog_public import PublicSearchParseError, parse_public_search

HTML = (
    Path(__file__).with_name("fixtures")
    / "public_search_jefferson_20261016.html"
).read_text()
URL = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)


@pytest.mark.parametrize("token", [" true ", " TRUE ", "\tTrUe\t"])
def test_aria_hidden_author_cannot_enter_public_provenance(token):
    before = parse_public_search(HTML, URL)
    assert '<a class="Author"' in HTML
    hostile = HTML.replace(
        '<a class="Author"',
        '<a aria-hidden="' + token + '" class="Author">PRIVATE_TOKEN</a>'
        '<a class="Author"', 1,
    )
    result = parse_public_search(hostile, URL)
    assert result.items[0].metadata["authors"] == before.items[0].metadata["authors"]
    assert "PRIVATE_TOKEN" not in repr(result.items)


def test_nested_hidden_title_child_never_leaks_even_with_spaced_aria():
    assert ">Jefferson prend des risques</a>" in HTML
    hostile = HTML.replace(
        ">Jefferson prend des risques</a>",
        '><span aria-hidden=" TRUE ">PRIVATE_TOKEN</span>'
        "Jefferson prend des risques</a>", 1,
    )
    with pytest.raises(PublicSearchParseError, match="hidden_field") as exc:
        parse_public_search(hostile, URL)
    assert "PRIVATE_TOKEN" not in str(exc.value)
