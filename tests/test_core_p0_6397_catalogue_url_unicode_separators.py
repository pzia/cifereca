"""#6397: Unicode line and paragraph separators cannot enter notice URLs."""
import pytest

from cifereca.catalogue_link import catalogue_url, catalogue_notice_url, safe_document_link_url


@pytest.mark.parametrize("character", [chr(0x2028), chr(0x2029)])
@pytest.mark.parametrize("encoded", [False, True, "double"])
def test_public_catalogue_url_rejects_line_separator(character, encoded):
    suffix = character if encoded is False else (
        "".join("%%%02X" % byte for byte in character.encode("utf-8"))
        if encoded is True else
        "".join("%%25%02X" % byte for byte in character.encode("utf-8"))
    )
    url = "https://example.org/books/123" + suffix
    assert catalogue_url({"friendly_url": url}) is None
    assert catalogue_notice_url({"friendly_url": url}) is None
    assert safe_document_link_url(url, relation="notice") is None


@pytest.mark.parametrize("character", [chr(0x2028), chr(0x2029)])
def test_notice_relation_rejects_line_separator(character):
    assert safe_document_link_url("https://example.org/books/123", relation="notice" + character) is None
