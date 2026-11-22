"""#6397: a root catalogue URL plus a query is not an attested book sheet.

Pure URL projection only: never a browser navigation or a provider request.
"""
import pytest

from cifereca.catalogue_link import catalogue_notice_url, catalogue_url, safe_document_link_url


@pytest.mark.parametrize("candidate", [
    "https://books.example/?q=roman",
    "https://books.example?search=42",
    "https://books.example/?ref=123",
])
def test_query_only_homepage_never_promoted_to_exact_book_notice(candidate):
    assert catalogue_url({"friendly_url": candidate}) == candidate
    assert catalogue_notice_url({"friendly_url": candidate}) is None
    assert safe_document_link_url(candidate, relation="notice") is None
    assert safe_document_link_url(candidate, relation="catalogue") == candidate


def test_root_with_query_does_not_mask_attested_independent_book_page():
    detail = "https://books.example/notices/123?lang=fr"
    row = {"friendly_url": "https://books.example/?q=roman",
           "metadata": {"product_url": detail}}
    assert catalogue_notice_url(row, base="https://books.example/") == detail


def test_relative_query_only_root_with_base_is_not_a_notice():
    assert catalogue_notice_url({"friendly_url": "/?q=roman"},
                                base="https://books.example/") is None
