"""Read-only EbooksGratuits holdings must not invent an EPUB offer (#2460).

No network, downloads, HTTP redirects or source mutation.
"""
from types import SimpleNamespace

import pytest

from cifereca_ebooksgratuits import EbooksSession


class OfflineClient:
    def __init__(self):
        self.calls = []
    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("holdings is a read-only projection")
    def close(self):
        pass


def _session():
    session = EbooksSession(
        credential_id="public", label="EbooksGratuits",
        credentials={}, options={}, config={}, diagnostics=None,
        transport=None,
    )
    session.client.close()
    offline = OfflineClient()
    session.client = offline
    return session, offline


def _book(**changes):
    values = {
        "record_id": "synthetic", "title": "Synthetic EPUB", "author": None,
        "type": "Livre numérique", "thumbnail_url": None,
        "source_provider": "ebooksgratuits", "download_url": None,
        "friendly_url": None, "downloadable": False, "format": None,
        "metadata": {"acquisition_source": "opds-calibre"},
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("changes", [
    {},
    {"download_url": "https://outside.test/index.php/fetch/book.epub",
     "downloadable": True, "format": "EPUB"},
    {"download_url": "https://opds2.ebooksgratuits.org/index.php/account/private.epub",
     "downloadable": True, "format": "EPUB"},
    {"download_url": "https://opds2.ebooksgratuits.org/index.php/fetch/book.epub",
     "downloadable": False, "format": "EPUB"},
    {"download_url": "https://opds2.ebooksgratuits.org/index.php/fetch/book.epub",
     "downloadable": True, "format": "PDF"},
    {"download_url": "https://opds2.ebooksgratuits.org/index.php/fetch/book.epub",
     "downloadable": True, "format": "EPUB", "source_provider": "other"},
])
def test_absent_untrusted_or_foreign_offer_is_unknown(changes):
    session, offline = _session()
    try:
        result = session.holdings(_book(**changes))
        assert result.availability_status == "unknown"
        assert result.availability_label == "Offre EPUB non vérifiée"
        assert result.holdings == []
        assert offline.calls == []
    finally:
        session.close()


def test_attested_opds_epub_offer_is_advertised_not_delivery():
    session, offline = _session()
    try:
        item = _book(
            download_url="https://opds2.ebooksgratuits.org/index.php/fetch/book.epub",
            friendly_url="https://opds2.ebooksgratuits.org/index.php/feed/old.xml",
            format="EPUB", downloadable=True,
        )
        detail = session.holdings(item)
        assert detail.availability_status == "available"
        assert "téléchargement non vérifié" in detail.availability_label
        assert offline.calls == []
    finally:
        session.close()


def test_legacy_html_epub_offer_needs_compatible_detail_url():
    session, offline = _session()
    try:
        item = _book(
            metadata={}, format="EPUB", downloadable=True,
            download_url="https://www.ebooksgratuits.com/newsendbook.php?format=epub",
            friendly_url="https://www.ebooksgratuits.com/details.php",
        )
        assert session.holdings(item).availability_status == "available"
        item.friendly_url = "https://www.ebooksgratuits.com/index.php/feed/old.xml"
        assert session.holdings(item).availability_status == "unknown"
        assert offline.calls == []
    finally:
        session.close()
