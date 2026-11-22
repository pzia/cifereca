"""#7163: invisible transparent HTML is not a public Numilog book fact."""
from pathlib import Path

import pytest

from cifereca_numilog_public import PublicSearchParseError, parse_public_search

HTML = (Path(__file__).with_name("fixtures") / "public_search_jefferson_20261016.html").read_text()
URL = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"


@pytest.mark.parametrize("style", ["opacity:0", "OPACITY: 0 !important",
                                    "opacity:0.0;", "color:red;opacity:0"])
def test_transparent_competing_author_is_not_attested(style):
    original = parse_public_search(HTML, URL)
    changed = HTML.replace(
        '<a class="Author"',
        f'<a style="{style}" class="Author">PRIVATE_SENTINEL</a><a class="Author"', 1,
    )
    assert changed != HTML
    parsed = parse_public_search(changed, URL)
    assert parsed.items[0].metadata["authors"] == original.items[0].metadata["authors"]
    assert "PRIVATE_SENTINEL" not in repr(parsed.items)


@pytest.mark.parametrize("style", ["opacity:0", "opacity:0.00!important"])
def test_transparent_descendant_of_title_fails_closed(style):
    changed = HTML.replace(
        ">Jefferson prend des risques</a>",
        f'><span style="{style}">PRIVATE_SENTINEL</span>Jefferson prend des risques</a>', 1,
    )
    assert changed != HTML
    with pytest.raises(PublicSearchParseError, match="hidden_field") as error:
        parse_public_search(changed, URL)
    assert "PRIVATE_SENTINEL" not in str(error.value)


def test_fractional_opacity_is_not_confused_with_hidden():
    original = parse_public_search(HTML, URL)
    changed = HTML.replace('<a class="Author"', '<a style="opacity:0.5" class="Author"', 1)
    assert parse_public_search(changed, URL).items[0].metadata["authors"] == original.items[0].metadata["authors"]
