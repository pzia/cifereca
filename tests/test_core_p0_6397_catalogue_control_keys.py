"""Catalogue URL boundary rejects decoded control characters in key names."""
import pytest

from cifereca.catalogue_link import catalogue_notice_url, catalogue_url, safe_document_link_url


@pytest.mark.parametrize("where", ["?", "#"])
@pytest.mark.parametrize("key", ["book%00", "book%2500", "book%252500", "bo%09ok", "bo%0aok", "bo%0dok", "book%7f"])
def test_decoded_control_in_parameter_name_fails_closed(where, key):
    url = f"https://catalogue.example/notice/42{where}{key}=42"
    row = {"friendly_url": url}
    assert catalogue_url(row) is None
    assert catalogue_notice_url(row) is None
    assert safe_document_link_url(url, relation="related") is None
    assert safe_document_link_url(url, relation="notice") is None


@pytest.mark.parametrize("suffix", ["?book=42", "?b%6fok=42", "?title=Le%20livre", "#edition=42"])
def test_valid_non_sensitive_url_remains_exact(suffix):
    url = "https://catalogue.example/notice/42" + suffix
    assert catalogue_url({"friendly_url": url}) == url
    assert catalogue_notice_url({"friendly_url": url}) == url


def test_bad_candidate_does_not_hide_independent_safe_notice():
    valid = "https://catalogue.example/notice/42"
    row = {"friendly_url": valid + "?book%2500=42", "product_url": valid}
    assert catalogue_notice_url(row) == valid
