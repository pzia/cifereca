"""#6397: reject scheme/port mismatches at catalogue link boundary."""
import pytest
from cifereca.catalogue_link import catalogue_url, catalogue_notice_url

@pytest.mark.parametrize("url", [
    "http://example.org:443/books/1",
    "https://example.org:80/books/1",
])
def test_reject_mismatched_scheme_port(url):
    assert catalogue_url({"friendly_url": url}) is None
    assert catalogue_notice_url({"friendly_url": url}) is None

@pytest.mark.parametrize("url", [
    "http://example.org:80/books/1",
    "https://example.org:443/books/1",
])
def test_accept_matching_scheme_port(url):
    assert catalogue_url({"friendly_url": url}) == url
    assert catalogue_notice_url({"friendly_url": url}) == url
