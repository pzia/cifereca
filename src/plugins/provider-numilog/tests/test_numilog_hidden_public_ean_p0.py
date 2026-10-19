"""#1072: invisible product DOM cannot attest digital EANs (offline)."""
import pytest

from cifereca_numilog_public_identifiers import extract_public_identifiers

URL = (
    "https://www.numilog.com/1171334/"
    "blackbone--coltan-song-tome-1--des-15-ans.ebook"
)
PAPER = "9782092591086"
EPUB = "9782092591093"


def identifiers(body, **options):
    return extract_public_identifiers(body, URL, **options)["identifiers"]


@pytest.mark.parametrize("wrapper", [
    '<div hidden>', '<div aria-hidden="true">',
    '<section style="display: none">',
    '<article style="visibility:hidden">',
    '<div style="display:none" style="display:block">',
    '<div aria-hidden="true" aria-hidden="false">',
])
def test_hidden_edition_labels_do_not_attest_epub(wrapper):
    tag = wrapper[1:].split()[0].rstrip(">")
    hidden = (
        wrapper + "<div><span>EAN EPUB SANS DRM</span><div>" + EPUB
        + "</div></div></" + tag + ">"
    )
    html = (
        "<h1>Blackbone</h1><h2>Détails</h2>"
        + hidden + "<div>EAN papier</div><div>" + PAPER + "</div>"
    )
    assert identifiers(html) == {"paper": PAPER}


def test_hidden_extra_h1_does_not_invalidate_real_product_title():
    html = (
        '<div hidden><h1>Autre livre</h1></div>'
        '<h1>Blackbone</h1><h2>Détails</h2>'
        '<dt>EAN papier</dt><dd>' + PAPER + '</dd>'
    )
    assert identifiers(html, expected_title="Blackbone") == {"paper": PAPER}


def test_void_hidden_input_does_not_suppress_subsequent_visible_details():
    html = (
        '<h1>Blackbone</h1><input hidden>'
        '<h2>Détails</h2><div>EAN EPUB SANS DRM</div><div>'
        + EPUB + '</div>'
    )
    assert identifiers(html) == {"epub": EPUB}


def test_canonical_product_form_marked_hidden_cannot_attest_identifiers():
    path = URL.split("www.numilog.com")[1]
    html = (
        '<form id="aspnetForm" name="aspnetForm" method="post"'
        ' action="' + path + '" hidden>'
        '<h1>Blackbone</h1><h2>Détails</h2>'
        '<div>EAN EPUB SANS DRM</div><div>' + EPUB
        + '</div></form>'
    )
    assert identifiers(
        html, expected_title="Blackbone", require_product_form=True,
    ) == {}
