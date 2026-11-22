"""#6397: malformed plugin relation cannot weaken the notice link boundary."""
import pytest

from cifereca.catalogue_link import safe_document_link_url


@pytest.mark.parametrize("relation", [" notice", "NOTICE ", "\tNoTiCe\n"])
def test_whitespace_padded_notice_relation_still_refuses_catalogue_root(relation):
    assert safe_document_link_url("https://books.example.org/", relation=relation) is None
    exact = "https://books.example.org/book/42"
    assert safe_document_link_url(exact, relation=relation) == exact


@pytest.mark.parametrize("relation", ["no\u200btice", "notice\u202e", "notice\x00"])
def test_invisible_relation_never_downgrades_notice_to_generic(relation):
    assert safe_document_link_url("https://books.example.org/", relation=relation) is None


def test_foreign_relation_is_not_coerced_in_gtk_url_sink():
    class Foreign:
        def __str__(self):
            raise AssertionError("foreign __str__ executed")

    assert safe_document_link_url("https://books.example.org/", relation=Foreign()) is None


def test_generic_catalogue_relation_still_allows_public_root():
    assert safe_document_link_url(
        "https://books.example.org/", relation="catalogue",
    ) == "https://books.example.org/"
