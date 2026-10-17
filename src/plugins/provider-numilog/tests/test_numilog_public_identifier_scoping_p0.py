"""P0 #1072: public product EANs cannot be borrowed from page chrome.

All HTML here is synthetic, based only on human-readable Numilog page text.
No real HTML capture, account credentials or HTTP activity is used.
"""
import pytest

from cifereca_numilog_public_identifiers import extract_public_identifiers

URL = "https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook"
PAPER = "9782092591086"
EPUB = "9782092591093"


def scan(body):
    return extract_public_identifiers("<html>" + body + "</html>", URL)["identifiers"]


def frame(body, *, after=""):
    return "<h1>Blackbone - Coltan song</h1>" + body + after


def details(ean=PAPER):
    return "<h2>Détails</h2><div>EAN papier</div><div>" + ean + "</div>"


def test_explicit_product_details_still_extracts_paper_and_epub():
    body = frame(
        "<p>Résumé du livre.</p><h2>Détails</h2>"
        "<div>EAN papier</div><div>" + PAPER + "</div>"
        "<h3>Informations ebook</h3>"
        "<div>EAN EPUB SANS DRM</div><div>" + EPUB + "</div>"
    )
    assert scan(body) == {"paper": PAPER, "epub": EPUB}


def test_real_page_style_inline_isbn_in_summary_before_generic_format_guide():
    body = frame(
        "<p>Résumé documenté.</p>"
        "<div>ISBN papier : 978-2-09259-108-6</div>"
        "<div>ISBN numérique : 978-2-09259-109-3</div>"
        "<h3>Guide des formats</h3>"
        "<p>Une liseuse EPUB, PDF, Adobe DRM et LCP.</p>"
        + details(),
    )
    assert scan(body) == {"paper": PAPER, "digital_unspecified": EPUB}


@pytest.mark.parametrize("wrapper", [
    "nav", "header", "footer", "aside", "form", "template", "script", "dialog",
])
def test_page_chrome_and_hidden_elements_are_not_book_evidence(wrapper):
    html = frame(
        f"<{wrapper}><p>EAN EPUB SANS DRM: {EPUB}</p></{wrapper}>"
        + details()
    )
    assert scan(html) == {"paper": PAPER}


def test_navigation_ean_before_product_h1_is_never_attributed_to_current_book():
    html = "<nav><div>EAN EPUB SANS DRM: " + EPUB + "</div></nav>" + frame(details())
    assert scan(html) == {"paper": PAPER}


def test_sidebar_and_recommendation_after_details_are_not_part_of_book():
    html = frame(
        details(),
        after=("<h2>Auteur(s) du livre</h2>"
               "<p>EAN EPUB SANS DRM: " + EPUB + "</p>"
               "<h2>Suggestions personnalisées</h2>"
               "<p>EAN EPUB: " + EPUB + "</p>"),
    )
    assert scan(html) == {"paper": PAPER}


def test_generic_guide_before_details_does_not_contribute_fake_format_ean():
    html = frame(
        "<p>Résumé.</p><h2>Guide des formats</h2>"
        "<div>EAN EPUB SANS DRM: " + EPUB + "</div>"
        + details()
    )
    assert scan(html) == {"paper": PAPER}


def test_unrecognized_page_without_h1_or_details_fails_closed():
    assert scan("<h2>Détails</h2><p>EAN papier: " + PAPER + "</p>") == {}
    assert scan("<h1>Blackbone</h1><p>EAN papier: " + PAPER + "</p>") == {}


def test_two_product_headlines_are_ambiguous_even_if_identifiers_match():
    assert scan(frame(details(), after="<h1>Autre livre</h1>")) == {}


def test_second_page_product_candidate_after_suggestions_cannot_be_associated():
    html = frame(
        details(), after=("<h2>Suggestions personnalisées</h2>"
                          "<h3>Une suggestion</h3>"
                          "<p>EAN EPUB SANS DRM: " + EPUB + "</p>"),
    )
    assert scan(html) == {"paper": PAPER}


def test_older_source_without_recognizable_detail_boundary_returns_no_ean():
    # Do not silently infer a purchased EPUB just because a detail URL exists.
    assert scan(frame("<p>EAN EPUB SANS DRM: " + EPUB + "</p>")) == {}
