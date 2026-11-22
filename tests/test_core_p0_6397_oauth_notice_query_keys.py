"""#6397: OAuth/SAML handoff parameters never become public web notice links."""
import pytest

from cifereca.catalogue_link import catalogue_notice_url, catalogue_url, safe_document_link_url


@pytest.mark.parametrize("key", [
    "client_secret", "oauth_code", "authorization_code", "code_verifier",
    "SAMLResponse", "SAMLRequest", "assertion", "client_assertion",
    "oauth_client_secret", "client%255fsecret",
])
@pytest.mark.parametrize("delimiter", ["?", "#"])
def test_account_handoff_query_keys_cannot_be_public_book_urls(key, delimiter):
    url = "https://books.example.org/notice/42" + delimiter + key + "=PRIVATE_SENTINEL"
    assert catalogue_url({"friendly_url": url}) is None
    assert catalogue_notice_url({"friendly_url": url}) is None
    assert safe_document_link_url(url, relation="notice") is None
    assert safe_document_link_url(url, relation="related") is None


def test_unqualified_handoff_does_not_hide_an_independent_public_book_link():
    safe = "https://books.example.org/notice/42?book=42"
    row = {"friendly_url": "https://books.example.org/notice/42?client_secret=x",
           "product_url": safe}
    assert catalogue_notice_url(row) == safe


@pytest.mark.parametrize("query", ["?book=42", "?code=42", "#edition=42"])
def test_normal_book_coordinates_are_unmodified(query):
    url = "https://books.example.org/notice/42" + query
    assert catalogue_notice_url({"friendly_url": url}) == url
