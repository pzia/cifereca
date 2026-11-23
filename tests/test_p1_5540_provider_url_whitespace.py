"""Regression: provider-declared account URLs must not contain whitespace.

Settings validation is local and must never perform provider I/O.
"""
import pytest

from cifereca.providers.base import AccountOptionField, valid_account_option_url


@pytest.mark.parametrize("url", [
    "https://example.org/path with space",
    "https://exa mple.org/path",
    "https://example.org/path\tsegment",
    "https://example.org/path\nsegment",
    "https://example.org/\u00a0segment",
])
def test_account_option_url_rejects_whitespace(url):
    field = AccountOptionField("endpoint", "Endpoint", value_type="url")
    assert not valid_account_option_url(field, url)


def test_account_option_url_still_accepts_valid_https_path():
    field = AccountOptionField("endpoint", "Endpoint", value_type="url")
    assert valid_account_option_url(field, "https://example.org/catalog/v1")
