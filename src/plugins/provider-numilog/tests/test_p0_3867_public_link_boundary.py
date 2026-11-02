"""P0 #3867: Numilog owns public URL qualification, Core owns generic safety.

No network, private HTML persistence, supplier URL in Core, or download.
"""
from __future__ import annotations

import pytest

from cifereca_numilog_html import public_book_url
from cifereca.core.search_orchestration import _numilog_public_catalog_url


def qualified(url):
    # Only the adapter's validated URL carries its owned-card marker.
    observed = public_book_url(url)
    if observed is None:
        return None
    return _numilog_public_catalog_url(
        observed, evidence="numilog.library_title_link",
    )


@pytest.mark.parametrize("url", [
    "https://www.numilog.com/31415/owned-edition.ebook",
    "https://www.numilog.com/ebooks/legacy-edition",
    "https://www.numilog.com/ebooks/legacy-edition?ref=library",
])
def test_attested_provider_public_notice_survives_core_projection(url):
    assert qualified(url) == url


@pytest.mark.parametrize("url", [
    "https://www.numilog.com/31415/owned-edition.ebook?token=abc",
    "https://www.numilog.com/31415/owned-edition.ebook?ref=library",
    "https://www.numilog.com/31415/owned-edition.pdf",
    "https://www.numilog.com/Pages/Compte/MesLivres.aspx",
    "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=1&m=dwld",
    "https://delivery.new-host.org/31415/owned-edition.ebook",
    "https://www.numilog.com/31415/../Pages/Compte/MesLivres.aspx",
    "http://www.numilog.com/31415/owned-edition.ebook",
])
def test_signed_private_or_unqualified_book_link_never_gets_provider_marker(url):
    assert qualified(url) is None


def test_caller_supplied_link_without_attested_producer_marker_is_not_public():
    assert _numilog_public_catalog_url(
        "https://www.numilog.com/31415/owned-edition.ebook",
    ) is None
