"""P0 #1072: ISBN/EAN must come from the exact public product WebForms form.

Only reduced, anonymous Numilog HTML is used; no account, ACSM, network or
guessing purchased EPUB/PDF editions. All inserted markup is synthetic.
"""
from pathlib import Path

import pytest

from cifereca_numilog_public_detail import parse_public_detail_jsonld, PublicDetailError
from cifereca_numilog_public_identifiers import extract_public_identifiers

ROOT = Path(__file__).parent / "fixtures"
URL = "https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook"
HTML = (ROOT / "observed_public_blackbone.html").read_text()
TITLE = "Blackbone - Coltan song- Tome 1 - Dès 15 ans"
PAPER = "9782092591086"
EPUB = "9782092591093"
FOREIGN = "9782380354621"


def parse(html):
    return parse_public_detail_jsonld(html, URL)["public_identifiers"]


def strict(html):
    return extract_public_identifiers(
        html, URL, expected_title=TITLE, require_product_form=True,
    )["identifiers"]


def test_observed_canonical_wrapper_keeps_distinct_paper_and_epub():
    assert parse(HTML) == {"paper": PAPER, "epub": EPUB}
    assert strict(HTML) == {"paper": PAPER, "epub": EPUB}


def test_extra_page_chrome_after_form_cannot_add_an_other_book_ean():
    poison = f'<h2>Détails</h2><p>EAN PDF: {FOREIGN}</p>'
    html = HTML.replace('</form></body>', '</form>' + poison + '</body>')
    assert parse(html) == {"paper": PAPER, "epub": EPUB}
    assert strict(html) == {"paper": PAPER, "epub": EPUB}


def test_ean_only_after_qualified_form_is_not_projected():
    start = HTML.index('<h2>Détails</h2>')
    end = HTML.index('</form>', start)
    html = (HTML[:start] + '</form><h2>Détails</h2>'
            + f'<div>EAN papier: {PAPER}</div><div>EAN EPUB: {EPUB}</div>'
            + HTML[end + len('</form>'):])
    assert parse(html) == {}
    assert strict(html) == {}


def test_header_before_form_is_not_the_current_product_evidence():
    poison = f'<h1>Une autre édition</h1><h2>Détails</h2><p>EAN PDF: {FOREIGN}</p>'
    html = HTML.replace('<body>', '<body>' + poison)
    assert parse(html) == {"paper": PAPER, "epub": EPUB}


@pytest.mark.parametrize("mutation", [
    "unclosed_form", "wrong_action", "wrong_method", "foreign_wrapper",
    "no_form",
])
def test_missing_or_incomplete_canonical_form_cannot_prove_edition(mutation):
    html = HTML
    if mutation == "unclosed_form":
        html = html.replace("</form>", "")
    if mutation == "wrong_action":
        html = html.replace('action="/1171334/', 'action="/1234567/')
    if mutation == "wrong_method":
        html = html.replace('method="post"', 'method="get"')
    if mutation == "foreign_wrapper":
        html = html.replace('id="aspnetForm"', 'id="loginForm"')
    if mutation == "no_form":
        html = html.replace(
            '<form name="aspnetForm" method="post" action="/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook" id="aspnetForm">',
            '',
        ).replace("</form>", "")
    assert parse(html) == {}
    assert strict(html) == {}


def test_jsonld_book_name_must_match_visible_heading():
    html = HTML.replace(
        'Blackbone - Coltan song- Tome 1 - Dès 15 ans</h1>',
        'Une autre édition</h1>',
    )
    assert parse(html) == {}
    assert strict(html) == {}


def test_duplicate_canonical_product_form_still_fails_closed():
    extra = ('<form id="aspnetForm" name="aspnetForm" method="post" '
             'action="/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook">'
             f'<h1>{TITLE}</h1><h2>Détails</h2>'
             f'<p>EAN papier: {PAPER}</p></form>')
    with pytest.raises(PublicDetailError, match="public_identifier_evidence_unverified"):
        parse_public_detail_jsonld(HTML.replace("</body>", extra + "</body>"), URL)


def test_bad_heading_does_not_block_jsonld_title_but_ean_stays_unverified():
    html = HTML.replace('<h1 class="Title Titlebook">', '<h1 class="Title Titlebook">Autre livre ')
    detailed = parse_public_detail_jsonld(html, URL)
    assert detailed["fields"]["title"] == TITLE
    assert detailed["public_identifiers"] == {}
    assert detailed["owned"] is False and detailed["downloadable"] is False
    assert detailed["download_url"] is None
