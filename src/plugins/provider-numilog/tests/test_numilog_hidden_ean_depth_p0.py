"""#1072: bounded ignored HTML ancestry for public EAN validation."""
import pytest

from cifereca_numilog_public_identifiers import (
    PublicIdentifierError, extract_public_identifiers,
)

URL = (
    "https://www.numilog.com/1171334/"
    "blackbone--coltan-song-tome-1--des-15-ans.ebook"
)
PAPER = "9782092591086"
EPUB = "9782092591093"


def scan(html):
    return extract_public_identifiers(html, URL)["identifiers"]


def test_deep_hidden_dom_is_rejected_under_one_megabyte_limit():
    html = (
        "<h1>Blackbone</h1><h2>Détails</h2>"
        "<div hidden>" + "<div>" * 513
        + "<div>EAN EPUB SANS DRM: " + EPUB + "</div>"
        + "</div>" * 513 + "</div>"
    )
    assert len(html) < 1024 * 1024
    with pytest.raises(PublicIdentifierError, match="hidden_html_too_deep"):
        scan(html)


def test_shallow_nested_hidden_dom_does_not_attest_epub():
    html = (
        "<h1>Blackbone</h1><h2>Détails</h2><div hidden>"
        + "<div>" * 20 + "<div>EAN EPUB SANS DRM: "
        + EPUB + "</div>" + "</div>" * 20
        + "</div><div>EAN papier</div><div>" + PAPER + "</div>"
    )
    assert scan(html) == {"paper": PAPER}


def test_ordinary_public_ean_is_not_blocked_by_hidden_depth_limit():
    html = (
        "<h1>Blackbone</h1><h2>Détails</h2>"
        "<div>EAN EPUB SANS DRM</div><div>" + EPUB + "</div>"
    )
    assert scan(html) == {"epub": EPUB}
