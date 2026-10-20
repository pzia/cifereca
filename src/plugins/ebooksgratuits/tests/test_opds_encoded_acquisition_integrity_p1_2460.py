"""#2460 P1 — deny OPDS acquisition paths that decode to other routes.

Tests are synthetic Atom, offline; no credentials, HTTP, DRM or actual
downloads. The currently working plain fetch routes remain accepted.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_ebooksgratuits import (
    EbooksSession, _opds_items, _safe_opds_link, _xml_root,
)

HOST = "https://opds2.ebooksgratuits.org"
FEED = HOST + "/index.php/feed"
EPUB = HOST + "/index.php/fetch/42.epub"

BAD_PATHS = [
    "/index.php/fetch/%2e%2e/account/private.epub",
    "/index.php/fetch/..%2faccount/private.epub",
    "/index.php/fetch/%2f..%2faccount/private.epub",
    "/index.php/fetch/%5caccount/private.epub",
    "/index.php/fetch/%252e%252e/account/private.epub",
    "/index.php/fetch/%252fprivate.epub",
    "/index.php/fetch/%255cprivate.epub",
    "/index.php/fetch/../account/private.epub",
]


class RecordingTransport:
    def __init__(self):
        self.calls = []

    def fetch(self, source, target):
        self.calls.append((source, target))
        return target


def session():
    provider = EbooksSession(
        credential_id="public", label="Public", credentials={},
        options={}, config={}, diagnostics=None,
        transport=RecordingTransport(),
    )
    provider.connect()
    return provider


def entry(url):
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<entry><id>urn:book:42</id><title>Exemple public</title>'
        '<link rel="http://opds-spec.org/acquisition/open-access" '
        'type="application/epub+zip" href="' + escape(url, {'"': '&quot;'}) + '"/>'
        '</entry></feed>'
    )


@pytest.mark.parametrize("raw", BAD_PATHS)
def test_ambiguous_encoded_download_paths_not_source_attested(raw):
    assert _safe_opds_link(FEED, raw, "download") is None


@pytest.mark.parametrize("raw", BAD_PATHS)
def test_forged_atom_acquisition_cannot_become_downloadable(raw):
    s = session()
    try:
        rows = _opds_items(s, _xml_root(entry(raw).encode()), FEED)
        assert rows == []
        assert s.transport.calls == []
    finally:
        s.close()


@pytest.mark.parametrize("raw", BAD_PATHS)
def test_poisoned_cached_download_is_refused_before_broker(tmp_path, raw):
    s = session()
    try:
        row = _opds_items(s, _xml_root(entry(EPUB).encode()), FEED)[0]
        row = deepcopy(row)
        row.download_url = HOST + raw
        with pytest.raises(ProviderCapabilityError, match="invalid_catalog_link") as err:
            s.download_direct(row, tmp_path / "unwritten")
        assert "account" not in str(err.value)
        assert s.transport.calls == []
        assert not (tmp_path / "unwritten").exists()
    finally:
        s.close()


@pytest.mark.parametrize("good", [
    "/index.php/fetch/42.epub",
    "/index.php/fetch/42.epub?download=1",
    "/fetch.php?id=42&format=epub",
    "/index.php/fetch?id=42",
])
def test_existing_public_fetch_routes_survive_without_new_network(good):
    s = session()
    try:
        assert _safe_opds_link(FEED, good, "download") == HOST + good
        rows = _opds_items(s, _xml_root(entry(good).encode()), FEED)
        assert len(rows) == 1
        assert rows[0].downloadable is True and rows[0].owned is False
        assert rows[0].download_url == HOST + good
        assert rows[0].friendly_url is None
        assert s.transport.calls == []
    finally:
        s.close()
