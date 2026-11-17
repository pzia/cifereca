"""#6590 shared XML primitive: no Core, session, HTTP or GTK required."""
import pytest

from cifereca.plugin_api.catalogue_xml import (
    CatalogueXMLParseError, parse_bounded_catalogue_xml,
    MAX_XML_BYTES, MAX_XML_DEPTH, MAX_XML_ELEMENTS,
)

ATOM = "{http://www.w3.org/2005/Atom}"


def check(content, code, **limits):
    with pytest.raises(CatalogueXMLParseError) as caught:
        parse_bounded_catalogue_xml(content, **limits)
    assert caught.value.code == code
    assert str(caught.value) == code
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("encoding", ("utf-8", "utf-8-sig", "utf-16", "utf-16-be", "utf-16-le"))
def test_atom_namespaces_and_raw_links_not_interpreted(encoding):
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Édition &amp; œuvre</title><link href="../page" rel="next"/></entry></feed>'
    root = parse_bounded_catalogue_xml(xml.encode(encoding))
    assert root.tag == ATOM + "feed"
    entry, = root.findall(ATOM + "entry")
    assert entry.findtext(ATOM + "title") == "Édition & œuvre"
    assert entry.find(ATOM + "link").get("href") == "../page"


@pytest.mark.parametrize("encoding", ("utf-8", "utf-8-sig", "utf-16", "utf-16-be", "utf-16-le"))
@pytest.mark.parametrize("doctype", (
    '<!DOCTYPE feed [<!ENTITY hidden "PRIVATE_PAYLOAD">]>',
    '<!DOCTYPE feed SYSTEM "file:///PRIVATE_PATH">',
    '<!DOCTYPE feed SYSTEM "https://PRIVATE.invalid">',
))
def test_dtd_never_allowed_in_supported_xml_encodings(encoding, doctype):
    check((doctype + "<feed/>").encode(encoding), "forbidden_doctype")


@pytest.mark.parametrize("encoding", ("utf-8", "utf-16", "utf-16-be", "utf-16-le"))
def test_html_not_a_feed_even_with_unqualified_or_invalid_markup(encoding):
    for xml in ("<html><body>login</body></html>",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
                " " * 700 + "<html><body>private login"):
        check(xml.encode(encoding), "html")


def test_exact_byte_depth_and_element_boundaries():
    xml = b"<feed><entry/></feed>"
    assert parse_bounded_catalogue_xml(xml, max_bytes=len(xml), max_depth=2, max_elements=2).tag == "feed"
    check(xml, "budget", max_bytes=len(xml)-1)
    check(xml, "budget", max_depth=1)
    check(xml, "budget", max_elements=1)
    check(b"<feed>" + b"<x>" * MAX_XML_DEPTH + b"</x>" * MAX_XML_DEPTH + b"</feed>", "budget")
    check(b"<feed>" + b"<x/>" * MAX_XML_ELEMENTS + b"</feed>", "budget")
    check(b" " * (MAX_XML_BYTES + 1), "budget")


def test_malformed_context_redacted_and_no_guess_about_sru_or_links():
    check(b"<feed><PRIVATE_PAYLOAD>", "malformed")
    check(b"", "budget")
    check(bytearray(b"<feed/>"), "budget")
    root = parse_bounded_catalogue_xml(b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><records/></searchRetrieveResponse>')
    assert root.tag.endswith("}searchRetrieveResponse")
    root = parse_bounded_catalogue_xml(b'<feed><link href="http://127.0.0.1/" /></feed>')
    # Policy remains the adapter's responsibility. Parser returns raw XML,
    # not a validated/fetchable URL, capability or authorization.
    assert root[0].get("href") == "http://127.0.0.1/"


@pytest.mark.parametrize("limits", (
    {"max_bytes": MAX_XML_BYTES + 1}, {"max_depth": MAX_XML_DEPTH+1},
    {"max_elements": MAX_XML_ELEMENTS+1}, {"max_bytes": True},
    {"max_depth": 0}, {"max_elements": 1.5},
))
def test_only_lower_budgets_allowed(limits):
    with pytest.raises(ValueError, match="invalid_catalogue_xml_budget"):
        parse_bounded_catalogue_xml(b"<feed/>", **limits)
