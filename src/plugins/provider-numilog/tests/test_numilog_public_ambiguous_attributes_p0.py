"""#1886: duplicate HTML evidence attributes are never chosen by dict().
The original page is a saved offline public HTML search fixture.
"""
from pathlib import Path

import pytest

from cifereca_numilog_public import PublicSearchParseError, parse_public_search

HTML = (
    Path(__file__).with_name('fixtures')
    / 'public_search_jefferson_20261016.html'
).read_text()
URL = (
    'https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx'
    '?mode=brSimple&query=jefferson'
)


@pytest.mark.parametrize('payload,anchor', [
    ('class="TitleBook" class="Author" href=', 'class="TitleBook" href='),
    ('<a class="Author" aria-hidden="true" aria-hidden="false"',
     '<a class="Author"'),
    ('<a class="Author" style="display:none" style="display:block"',
     '<a class="Author"'),
    ('class="TitleBook" href="/123/wrong.ebook" href=',
     'class="TitleBook" href='),
    ('<a class="Author" hidden hidden', '<a class="Author"'),
])
def test_duplicate_evidence_attribute_is_rejected_not_silently_overwritten(
    payload, anchor,
):
    assert anchor in HTML
    hostile = HTML.replace(anchor, payload, 1)
    with pytest.raises(PublicSearchParseError, match='ambiguous_html_attribute'):
        parse_public_search(hostile, URL)


def test_original_public_html_still_parses():
    page = parse_public_search(HTML, URL)
    assert len(page.items) == 20
    assert page.total_results >= 20
