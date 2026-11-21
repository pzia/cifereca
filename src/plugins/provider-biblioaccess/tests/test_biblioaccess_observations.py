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


@pytest.mark.parametrize("slug", [
    "%2e%2e", "%252e%252e", "title%2Fother", "title%5Cother",
    "title%3Ftoken", "title%23fragment", "title%3Bparam", "title%00",
    "title%0A", "title%FF", "title%2", "title.epub", "title%2Eacsm",
    "title.lcpl", "title.pdf", "title.zip",
])
def test_book_link_cannot_change_route_or_be_an_acquisition(slug):
    page = "https://www.biblioaccess.com/1409/Catalog/Search?query=test"
    href = "/1409/Catalog/Book/123/" + slug
    assert mod.book_id_from_url("https://www.biblioaccess.com" + href) is None
    parsed = mod.extract_catalogue_links(
        page, '<a href="' + href + '">Ambiguous</a>'
              '<a href="/1409/Catalog/Book/456/valid">Valid</a>',
    )
    assert parsed.book_ids == ("456",)


@pytest.mark.parametrize("href", [
    "/1409/Catalog/Book/123/../456",
    "/1409/Catalog/Book/123/ti\ttle",
    "https://@www.biblioaccess.com/1409/Catalog/Book/123/title",
    "https://[invalid/1409/Catalog/Book/123/title",
])
def test_invalid_raw_href_does_not_normalize_into_book_or_abort_page(href):
    result = mod.extract_catalogue_links(
        "https://www.biblioaccess.com/1409/Catalog/Search?query=test",
        '<a href="' + href + '">Invalid</a>'
        '<a href="/1409/Catalog/Book/789/title">Valid</a>',
    )
    assert result.book_ids == ("789",)


@pytest.mark.parametrize("suffix", ["", "/", "/titre", "/l%C3%A9t%C3%A9", "/un%20titre"])
def test_ordinary_book_slug_keeps_original_attested_url(suffix):
    url = "https://www.biblioaccess.com/1409/Catalog/Book/123" + suffix
    assert mod.book_id_from_url(url) == "123"
    parsed = mod.extract_catalogue_links(
        "https://www.biblioaccess.com/1409/Catalog/Search?query=test",
        '<a href="' + url + '">Notice</a>',
    )
    assert parsed.book_urls == (("123", url),)


def test_next_page_keeps_exact_filter_scope_and_first_equivalent_url():
    page = "https://www.biblioaccess.com/1409/Catalog/Search?query=test&type=all&language=fr&pageindex=0"
    base = "/1409/Catalog/Search?"
    wrong = base + "query=test&type=all&pageindex=1"
    first = base + "query=test&type=all&language=fr&pageindex=1"
    second = base + "pageindex=1&language=fr&type=all&query=test"
    def anchor(url):
        return '<a rel="next" href="' + url.replace("&", "&amp;") + '">Suivant</a>'
    links = mod.extract_catalogue_links(page, anchor(wrong) + anchor(first) + anchor(second))
    assert links.next_page_url == "https://www.biblioaccess.com" + first


def test_next_page_conflict_never_selects_last_candidate():
    page = "https://www.biblioaccess.com/1409/Catalog/Search?query=test&pageindex=0"
    first = '<a rel="next" href="/1409/Catalog/Search?query=test&amp;pageindex=1">Suivant</a>'
    second = first.replace("pageindex=1", "pageindex=01")
    links = mod.extract_catalogue_links(page, first + second + first)
    assert links.next_page_url is None and links.completeness == "partial"


@pytest.mark.parametrize("wrapper", [
    "<template>{body}</template>",
    "<noscript>{body}</noscript>",
    "<div hidden>{body}</div>",
    "<div aria-hidden='true'>{body}</div>",
    "<div class='visually-hidden'>{body}</div>",
    "<div class='d-none'>{body}</div>",
    "<div style='display: none !important'>{body}</div>",
    "<div style='visibility: hidden'>{body}</div>",
    "<div hidden hidden='false'>{body}</div>",
    "<div class='hidden' class='shown'>{body}</div>",
    "<head>{body}</head>",
])
def test_hidden_catalogue_markup_never_promotes_book_or_pager(wrapper):
    # A template/hidden supplier card is not a public bibliographic fact.
    source = ("https://www.biblioaccess.com/1409/Catalog/Search"
              "?query=test&pageindex=0")
    hidden = (
        '<a href="/1409/Catalog/Book/999/preview">Faux livre</a>'
        '<a href="/1409/Catalog/Search?query=test&pageindex=001">Suivant</a>'
    )
    visible = (
        '<a href="/1409/Catalog/Book/123/valid">Livre confirmé</a>'
        '<a href="/1409/Catalog/Search?query=test&pageindex=1">Suivant</a>'
    )
    result = mod.extract_catalogue_links(source, wrapper.format(body=hidden) + visible)
    assert result.book_ids == ("123",)
    assert result.book_titles == (("123", "Livre confirmé"),)
    assert result.next_page_url == (
        "https://www.biblioaccess.com/1409/Catalog/Search"
        "?query=test&pageindex=1"
    )


def test_hidden_child_text_does_not_contaminate_a_visible_book_title():
    source = "https://www.biblioaccess.com/1409/Catalog/Search?query=test"
    html = (
        '<a href="/1409/Catalog/Book/123/valid">Livre '
        '<span hidden>Jeton privé</span>confirmé</a>'
    )
    result = mod.extract_catalogue_links(source, html)
    assert result.book_ids == ("123",)
    assert result.book_titles == (("123", "Livre confirmé"),)


def test_overdeep_catalogue_markup_refuses_all_documentary_evidence():
    source = "https://www.biblioaccess.com/1409/Catalog/Search?query=test"
    html = (
        '<a href="/1409/Catalog/Book/111/first">Avant</a>'
        + "<div>" * 129
        + '<a href="/1409/Catalog/Book/222/nested">Non attesté</a>'
        + "</div>" * 129
    )
    result = mod.extract_catalogue_links(source, html)
    assert result.book_ids == ()
    assert result.next_page_url is None
    assert result.completeness == "partial"
