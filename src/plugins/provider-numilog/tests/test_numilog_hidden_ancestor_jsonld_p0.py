"""#1886: hidden DOM ancestry is not public JSON-LD Book evidence."""
import json

import pytest

from cifereca_numilog_public_detail import (
    _Scripts, PublicDetailError, parse_public_detail_jsonld,
)

URL = "https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook"


def book(title):
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "Book", "url": URL, "name": title})
        + "</script>"
    )


@pytest.mark.parametrize("opening", [
    "<div hidden>",
    "<section aria-hidden='true'>",
    "<article style='display: none'>",
    "<div style='visibility: hidden'>",
    "<div style='color: blue' style='display:none'>",
    "<section aria-hidden='false' aria-hidden='TRUE'>",
])
def test_hidden_wrapper_is_not_book_evidence(opening):
    html = "<html><head>" + book("Genuine") + "</head><body>"
    html += opening + book("Wrong") + '</' + opening[1:].split()[0].rstrip('>') + '>'
    html += "</body></html>"
    assert parse_public_detail_jsonld(html, URL)["fields"]["title"] == "Genuine"


@pytest.mark.parametrize("opening", [
    "<div hidden>", "<section aria-hidden='true'>",
    "<article style='display:none'>",
])
def test_hidden_book_alone_is_not_a_public_product(opening):
    tag = opening[1:].split()[0].rstrip(">")
    html = opening + book("PRIVATE_WRONG") + "</" + tag + ">"
    with pytest.raises(PublicDetailError, match="missing_or_ambiguous_book"):
        parse_public_detail_jsonld(html, URL)


def test_invisible_script_element_does_not_override_visible_head_jsonld():
    html = book("Genuine") + (
        '<script hidden type="application/ld+json">'
        '{"@type":"Book","url":"' + URL + '","name":"Wrong"}'
        "</script>"
    )
    assert parse_public_detail_jsonld(html, URL)["fields"]["title"] == "Genuine"


def test_nested_same_named_hidden_containers_remain_blocked():
    hidden = "<div hidden><div>" + book("Wrong") + "</div></div>"
    assert parse_public_detail_jsonld(
        "<head>" + book("Genuine") + "</head>" + hidden, URL
    )["fields"]["title"] == "Genuine"


def test_void_hidden_elements_do_not_suppress_following_public_book():
    html = (
        "<html><head><meta hidden><link aria-hidden='true'>"
        + book("Genuine") + "</head></html>"
    )
    assert parse_public_detail_jsonld(html, URL)["fields"]["title"] == "Genuine"


def test_malformed_unrelated_end_tag_cannot_unhide_a_book():
    reader = _Scripts()
    reader.feed("<div hidden></section>" + book("Wrong") + "</div>"
                + book("Genuine"))
    reader.close()
    assert len(reader.scripts) == 1
    assert "Genuine" in reader.scripts[0]
