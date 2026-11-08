"""Pure synthetic routing/catalogue/ACSM tests; no credentials or real XML."""
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "biblioaccess_observations.py"
spec = importlib.util.spec_from_file_location("biblioaccess_observations", MODULE)
mod = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_search_route():
    url = mod.search_summary_url("Les livres & les mots")
    assert url.endswith("query=Les+livres+%26+les+mots")
    assert mod.classify_search_response(
        request_url=url, status=302,
        location="/1409/Catalog/Search?query=Les+livres+%26+les+mots&type=all&pageindex=0",
    ) == "search_redirect"
    assert mod.classify_search_response(
        request_url=url, status=302,
        location="https://malicious.example/1409/Catalog/Search",
    ) == "untrusted_redirect"
    for location in (
        "/1409/Catalog/Search?query=different&pageindex=0",
        "/1409/Catalog/Search?query=Les+livres+%26+les+mots&pageindex=200",
        "/1409/Catalog/Search?query=Les+livres+%26+les+mots&pageindex=0&ticket=SYNTHETIC",
        "/1717/Catalog/Search?query=Les+livres+%26+les+mots&pageindex=0",
    ):
        assert mod.classify_search_response(
            request_url=url, status=302, location=location
        ) == "untrusted_redirect"
    assert mod.classify_search_response(request_url=url, status=200) == "html_unparsed"
    assert mod.classify_search_response(request_url=url, status=401) == "authentication_required"
    assert mod.classify_search_response(request_url=url, status=403) == "authentication_required"
    assert mod.classify_search_response(request_url=url, status=429) == "rate_limited"
    assert mod.classify_search_response(request_url=url, status=503) == "remote_error"
    with pytest.raises(ValueError):
        mod.search_summary_url(" ")


def test_catalogue_is_partial_even_without_cards():
    url = "https://www.biblioaccess.com/1409/Catalog/Search?query=test&pageindex=0"
    html = """
    <a href="/1409/Catalog/Book/123/example">Premier livre</a>
    <a href="/1409/Catalog/Book/123/example">Doublon</a>
    <a href="https://evil.example/1409/Catalog/Book/777">Mauvais domaine</a>
    <a href="/1717/Catalog/Book/999">Autre établissement</a>
    <a href="/1409/Catalog/Search?query=test&pageindex=1">Suiv &gt;</a>
    """
    found = mod.extract_catalogue_links(url, html)
    assert found.book_ids == ("123",)
    assert found.book_urls == (("123", "https://www.biblioaccess.com/1409/Catalog/Book/123/example"),)
    assert found.next_page_url.endswith("pageindex=1")
    assert found.completeness == "partial"
    empty = mod.extract_catalogue_links(url, "<p>Aucune carte trouvée</p>")
    assert empty.book_ids == ()
    assert empty.book_urls == ()
    assert empty.completeness == "partial"
    assert mod.book_id_from_url("https://www.biblioaccess.com/1409/Catalog/Book/123/title") == "123"
    assert mod.book_id_from_url("https://www.biblioaccess.com/1717/Catalog/Book/123") is None


@pytest.mark.parametrize("href", [
    "https://evil.example/1409/Catalog/Search?query=q&pageindex=1",
    "https://www.biblioaccess.com/1409/Catalog/Search?query=q&pageindex=999999",
    "https://www.biblioaccess.com/1409/Catalog/Search?query=q&pageindex=1&ticket=SYNTHETIC",
    "https://www.biblioaccess.com/1409/Catalog/Search?query=different&pageindex=1",
    "https://www.biblioaccess.com/1409/Catalog/Search?query=q&pageindex=0",
    "https://www.biblioaccess.com/1409/Catalog/Search?query=q&pageindex=2",
    "https://www.biblioaccess.com/1409/Catalog/Search?query=q&pageindex=1&pageindex=1",
])
def test_untrusted_next_href(href):
    url = "https://www.biblioaccess.com/1409/Catalog/Search?query=q&pageindex=0"
    parsed = mod.extract_catalogue_links(url, '<a href="' + href + '">Suiv</a>')
    assert parsed.next_page_url is None


def test_acsm_structure_is_not_a_right_or_download():
    document = b"""<?xml version="1.0"?>
    <fulfillmentToken xmlns="http://ns.adobe.com/adept" fulfillmentType="loan">
     <operatorURL>http://synthetic.invalid/path?token=SECRET-SYNTHETIC</operatorURL>
     <licenseToken><permissions>
       <display><duration>2419200</duration></display>
       <play><duration>2419200</duration></play>
     </permissions></licenseToken>
    </fulfillmentToken>"""
    result = mod.inspect_acsm_structure(document)
    assert result.kind == "loan"
    assert result.drm == "adobe-adept"
    assert result.format == "acsm"
    assert result.duration_display_seconds == 2419200
    assert result.duration_play_seconds == 2419200
    assert not result.content_available
    assert not result.fulfillment_complete
    assert "SECRET-SYNTHETIC" not in repr(result)


@pytest.mark.parametrize("data", [
    b"", b"<html></html>",
    b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><x>&x;</x>',
    b"x" * 128001,
])
def test_acsm_fail_closed(data):
    with pytest.raises(ValueError):
        mod.inspect_acsm_structure(data)
