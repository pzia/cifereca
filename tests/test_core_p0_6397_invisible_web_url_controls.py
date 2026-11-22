"""#6397: invisible Unicode/bidi and nested URL controls are not book links."""
import pytest

from cifereca.catalogue_link import (
    catalogue_url, catalogue_notice_url, safe_document_link_url,
)


@pytest.mark.parametrize("suffix", [
    "/\u202eother",
    "/\u200bhidden",
    "?book=%E2%80%AEother",
    "?book=%25E2%2580%25AEother",
    "?book=%2525E2%252580%2525AEother",
    "?book=%0dextra",
    "?book=%250aextra",
    "?book=%25250aextra",
    "#%E2%81%A6other",
    "#%E2%81%A9other",
])
def test_url_control_cannot_be_public_book_reference(suffix):
    unsafe = "https://books.example.org/notice/42" + suffix
    assert catalogue_url({"friendly_url": unsafe}) is None
    assert catalogue_notice_url({"friendly_url": unsafe}) is None
    assert safe_document_link_url(unsafe, relation="notice") is None
    assert safe_document_link_url(unsafe, relation="related") is None


def test_unsafe_link_does_not_mask_independent_safe_product_link():
    safe = "https://books.example.org/notice/42?book=Le%20Comte"
    assert catalogue_notice_url({
        "friendly_url": "https://books.example.org/notice/42?book=%E2%80%AE",
        "product_url": safe,
    }) == safe


@pytest.mark.parametrize("suffix", [
    "/Fran%C3%A7ais", "?book=Le%20Comte", "?book=%C3%89dition",
    "#edition=livre",
])
def test_legitimate_unicode_and_percent_encoded_spaces_survive(suffix):
    url = "https://books.example.org/notice/42" + suffix
    assert catalogue_notice_url({"friendly_url": url}) == url


def test_relative_book_link_does_not_bypass_encoded_bidi_fence():
    assert catalogue_notice_url(
        {"friendly_url": "/notice/42?book=%E2%80%AE"},
        base="https://books.example.org/catalogue",
    ) is None
