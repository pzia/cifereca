"""#7163: CSS-hidden Numilog search evidence is not a public Notice C fact.

Only saved, sanitized catalogue HTML; no HTTP or account action.
"""
from pathlib import Path

import pytest

from cifereca_numilog_public import PublicSearchParseError, parse_public_search

HTML = (Path(__file__).with_name("fixtures") / "public_search_jefferson_20261016.html").read_text()
URL = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"


@pytest.mark.parametrize("klass", ["hidden", "d-none", "sr-only", "visually-hidden"])
def test_css_hidden_competing_author_is_never_published(klass):
    original = parse_public_search(HTML, URL)
    changed = HTML.replace(
        '<a class="Author"',
        f'<a class="Author {klass}">PRIVATE_SENTINEL</a><a class="Author"', 1,
    )
    assert changed != HTML
    actual = parse_public_search(changed, URL)
    assert actual.items[0].metadata["authors"] == original.items[0].metadata["authors"]
    assert "PRIVATE_SENTINEL" not in repr(actual.items)


@pytest.mark.parametrize("klass", ["hidden", "d-none", "sr-only", "visually-hidden"])
def test_css_hidden_nested_title_text_fails_closed(klass):
    changed = HTML.replace(
        ">Jefferson prend des risques</a>",
        f'><span class="{klass}">PRIVATE_SENTINEL</span>Jefferson prend des risques</a>', 1,
    )
    assert changed != HTML
    with pytest.raises(PublicSearchParseError, match="hidden_field") as exc:
        parse_public_search(changed, URL)
    assert "PRIVATE_SENTINEL" not in str(exc.value)
