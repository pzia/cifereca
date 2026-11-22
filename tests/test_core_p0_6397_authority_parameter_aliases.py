"""#6397: access/SSO credential aliases are not public documentary links."""
import pytest

from cifereca.catalogue_link import (
    catalogue_url, catalogue_notice_url, safe_document_link_url,
)


@pytest.mark.parametrize("parameter", [
    "access_key", "access-key", "secret_key", "auth_code", "sso_ticket",
    "AccessKey", "secret%5Fkey", "AUTH%255Fcode", "sso%2Dticket",
])
@pytest.mark.parametrize("delimiter", ["?", "#"])
def test_authority_alias_never_enters_public_book_link(parameter, delimiter):
    unsafe = "https://books.example.org/notice/42" + delimiter + parameter + "=PRIVATE"
    assert catalogue_url({"friendly_url": unsafe}) is None
    assert catalogue_notice_url({"friendly_url": unsafe}) is None
    assert safe_document_link_url(unsafe, relation="notice") is None


def test_rejected_sensitive_candidate_does_not_hide_separate_public_url():
    safe = "https://books.example.org/notice/42?book=42"
    assert catalogue_notice_url({
        "friendly_url": "https://books.example.org/notice/42?access_key=PRIVATE",
        "product_url": safe,
    }) == safe


@pytest.mark.parametrize("parameter", ["accessibility", "accession", "sso_title", "authorship"])
def test_documentary_words_must_not_be_mistaken_for_credentials(parameter):
    safe = "https://books.example.org/notice/42?" + parameter + "=42"
    assert catalogue_notice_url({"friendly_url": safe}) == safe
