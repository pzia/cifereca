"""#1599: independent public notice survives unsafe friendly links.

Pure headless projection test: no navigation, credentials, network or GTK.
"""
import pytest

from recommendations import _public_recommendation_notice


@pytest.mark.parametrize("friendly,notice,expected", [
    ("https://books.example.org/friendly/1", "https://books.example.org/notice/1", "https://books.example.org/friendly/1"),
    ("", "https://books.example.org/notice/1", "https://books.example.org/notice/1"),
    ("https://books.example.org/download/secret.epub", "https://books.example.org/notice/1", "https://books.example.org/notice/1"),
    ("https://books.example.org/book?token=PRIVATE", "https://books.example.org/notice/1", "https://books.example.org/notice/1"),
    ("http://localhost:8080/private", "https://books.example.org/notice/1", "https://books.example.org/notice/1"),
    ("https://books.example.org/friendly/1", "https://books.example.org/download/secret.epub", "https://books.example.org/friendly/1"),
    ("", "https://books.example.org/download/secret.epub", None),
    ("https://books.example.org/book?token=PRIVATE", "http://127.0.0.1/notice", None),
    ("", "", None),
])
def test_only_independently_validated_public_pages_are_exported(friendly, notice, expected):
    assert _public_recommendation_notice({
        "friendly_url": friendly, "notice_url": notice,
    }) == expected


class Poison:
    def __str__(self):
        raise AssertionError("unsafe URL type must never be coerced")


def test_non_string_friendly_url_does_not_mask_public_notice():
    assert _public_recommendation_notice({
        "friendly_url": Poison(),
        "notice_url": "https://books.example.org/notice/2",
    }) == "https://books.example.org/notice/2"


def test_download_coordinates_are_never_used_as_fallback():
    assert _public_recommendation_notice({
        "friendly_url": "https://books.example.org/download/secret.epub",
        "notice_url": "",
        "download_url": "https://books.example.org/book/unsafe",
        "acquisition_url": "https://books.example.org/book/unsafe",
    }) is None
