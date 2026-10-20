"""P0 #2024: four owned cards, no streaming, and one DRM-free direct EPUB.

This is a transport/ownership regression with fictional HTML and a fake EPUB.
It is NOT evidence of the actual merchant's delivery for the user's purchase.
No ACSM is requested, imported, or retried.
"""
from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from tests.test_numilog_signed_acsm_transport_p0 import setup, _assert_clean

LIBRARY = "https://www.numilog.com/Pages/Compte/MesLivres.aspx"
READ_FOURTH = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=78&m=dwld"
INVENTORY = (
    '<html><body><table id="myTable">'
    + "".join(
        '<div><span class="pTitle">Achat fictif ' + str(lid) + '</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid='
        + str(lid) + '&amp;m=dwld">Télécharger</a></div>'
        for lid in (12, 34, 56, 78)
    )
    + '</table></body></html>'
)


def fake_epub_bytes() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip",
                         compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
            '</rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", '<package version="3.0"/>')
    return stream.getvalue()


@pytest.mark.parametrize("mime", ("application/epub+zip", "application/octet-stream"))
def test_four_owned_no_stream_drmfree_epub_uses_direct_validation_no_adept(tmp_path, mime):
    requested = []
    payload = fake_epub_bytes()

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY,
                                  headers={"content-type": "text/html"})
        if url == READ_FOURTH:
            return httpx.Response(200, content=payload,
                                  headers={"content-type": mime})
        pytest.fail("No other purchase, streaming, ACSM or external GET allowed")

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        items = session.library()
        assert len(items) == 4
        assert all(item.owned is True and item.downloadable is True
                   and item.download_url is None for item in items)
        assert requested == [LIBRARY]
        selected = next(item for item in items if item.title == "Achat fictif 78")
        path, origin = session.download_direct(selected, tmp_path)
        assert path.is_file() and path.read_bytes() == payload
        assert origin == LIBRARY
        assert requested == [LIBRARY, LIBRARY, READ_FOURTH]
        assert importer.calls == []  # An unprotected EPUB never enters ADEPT.
        terminal = [kw["detail"] for args, kw in diagnostics.events
                    if args[:2] == ("NUMILOG", "acquisition_delivery")
                    and kw["detail"].get("phase") == "completed"]
        assert len(terminal) == 1
        assert terminal[0]["code"] == "epub_complete"
        assert terminal[0]["status"] == "ok"
        assert not any("stream" in url or "acsm" in url for url in requested)
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_owned_no_stream_corrupt_epub_is_not_a_saved_purchase(tmp_path):
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY)
        if url == READ_FOURTH:
            return httpx.Response(200, content=b"not an epub",
                                  headers={"content-type": "application/epub+zip"})
        pytest.fail("No secondary delivery should be requested")

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        selected = next(item for item in session.library()
                        if item.title == "Achat fictif 78")
        with pytest.raises(ProviderError, match="EPUB valide"):
            session.download_direct(selected, tmp_path)
        assert requested == [LIBRARY, LIBRARY, READ_FOURTH]
        assert importer.calls == []
        assert not list(tmp_path.glob("*.epub"))
        _assert_clean(tmp_path)
        final = [kw["detail"] for args, kw in diagnostics.events
                 if args[:2] == ("NUMILOG", "acquisition_delivery")
                 and kw["detail"].get("phase") == "completed"]
        assert len(final) == 1 and final[0]["status"] == "error"
    finally:
        session.close()
