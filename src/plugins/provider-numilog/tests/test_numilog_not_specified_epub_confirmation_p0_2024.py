"""#2024 — authenticated Numilog purchases can expose a signed DRM-free EPUB.

Every book, product, order, signature, endpoint response, and session is fake.
No merchant call, real signed URL, license, or local user's file is reused.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_html import (
    DeliverySelectionError, _is_direct_epub_download, delivery_url,
)
from tests.test_numilog_confirmation_structure_p0 import ALLOWED
from tests.test_numilog_no_stream_confirmation_p0 import (
    confirmation_without_stream, LANDING,
)
from tests.test_numilog_no_stream_drmfree_epub_p0_2024 import (
    INVENTORY, READ_FOURTH, LIBRARY, fake_epub_bytes,
)
from tests.test_numilog_signed_acsm_transport_p0 import (
    SIGNED as SIGNED_ACSM, _assert_clean, setup,
)

SIGNED_EPUB = (
    "https://services.numilog.com/v4.0/HandlerDownloadLinks/HandlerDilicomDownloadLink.ashx"
    "?productid=9001&idcmd=42&date=01%2f01%2f2000+00%3a00%3a00&hash=FAKE_SIGNATURE%3d"
)
ALLOW_EPUB = ALLOWED | {("https", "services.numilog.com", 443)}


def confirmation(*, login_widget=False):
    html = confirmation_without_stream(login_widget=login_widget)
    assert html.count("DrmTechnlology: 'ADOBE', link:") == 1
    assert html.count(SIGNED_ACSM) == 1
    return (
        html.replace("DrmTechnlology: 'ADOBE', link:",
                     "DrmTechnlology: 'NOT_SPECIFIED', format: 'EPUB', link:")
        .replace(SIGNED_ACSM, SIGNED_EPUB)
    )


def completed_events(diagnostics):
    return [
        (args[2], kw["detail"])
        for args, kw in diagnostics.events
        if args[:2] == ("NUMILOG", "acquisition_delivery")
        and kw["detail"].get("phase") == "completed"
    ]


def test_confirmation_direct_epub_requires_fresh_owned_binding():
    with pytest.raises(DeliverySelectionError):
        delivery_url(confirmation(), LANDING, allowed_origins=ALLOW_EPUB,
                     expected_readbook=READ_FOURTH,
                     require_bound_confirmation=True)
    evidence = {}
    assert delivery_url(
        confirmation(), LANDING, allowed_origins=ALLOW_EPUB,
        expected_readbook=READ_FOURTH, require_bound_confirmation=True,
        allow_missing_readbook=True, evidence=evidence,
    ) == SIGNED_EPUB
    assert evidence["attested_acsm_candidates"] == 0
    assert "FAKE_SIGNATURE" not in repr(evidence)
    assert not _is_direct_epub_download(SIGNED_EPUB, ALLOWED)


@pytest.mark.parametrize("mime", ["application/epub+zip", "application/octet-stream"])
@pytest.mark.parametrize("login_widget", [False, True])
def test_302_owned_fourth_no_stream_direct_epub_never_calls_adept(
        tmp_path, mime, login_widget):
    visited = []
    payload = fake_epub_bytes()
    html = confirmation(login_widget=login_widget)

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY,
                                  headers={"content-type": "text/html"})
        if url == READ_FOURTH:
            return httpx.Response(302, headers={"location": LANDING})
        if url == LANDING:
            return httpx.Response(200, text=html,
                                  headers={"content-type": "text/html"})
        if url == SIGNED_EPUB:
            assert request.headers.get("referer") == "https://www.numilog.com/"
            return httpx.Response(200, content=payload,
                                  headers={"content-type": mime})
        pytest.fail("No other purchase, stream, LCP, ACSM or off-origin GET")

    session, diagnostics, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        books = session.library()
        assert len(books) == 4
        selected = next(x for x in books if x.title == "Achat fictif 78")
        local, source = session.download_direct(selected, tmp_path)
        assert source == LIBRARY
        assert local.read_bytes() == payload
        assert visited == [LIBRARY, LIBRARY, READ_FOURTH, LANDING, SIGNED_EPUB]
        assert broker.calls == []
        assert completed_events(diagnostics)[0][0] == "ok"
        assert completed_events(diagnostics)[0][1]["code"] == "epub_complete"
        assert "FAKE_SIGNATURE" not in repr(diagnostics.events)
        assert "idcmd=" not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_direct_confirmation_href_is_accepted_only_from_fresh_owned_card(tmp_path):
    needle = (
        '<a class="LnkDownloadBook" href="'
        '/Pages/Compte/readBook.ashx?lid=78&amp;m=dwld">Télécharger</a></div>'
    )
    assert INVENTORY.count(needle) == 1
    with_href = INVENTORY.replace(
        needle, needle.replace("</div>", f'<a href="{LANDING}">Commande</a></div>'),
    )
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=with_href)
        if url == LANDING:
            return httpx.Response(200, text=confirmation(),
                                  headers={"content-type": "text/html"})
        if url == SIGNED_EPUB:
            return httpx.Response(200, content=fake_epub_bytes(),
                                  headers={"content-type": "application/epub+zip"})
        pytest.fail("Direct owned card must not fall back to unbound readBook")

    session, diagnostics, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        selected = next(x for x in session.library()
                        if x.title == "Achat fictif 78")
        result, _ = session.download_direct(selected, tmp_path)
        assert result.is_file()
        assert seen == [LIBRARY, LIBRARY, LANDING, SIGNED_EPUB]
        assert broker.calls == []
        assert completed_events(diagnostics)[0][1]["code"] == "epub_complete"
        assert "FAKE_SIGNATURE" not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize("mutation", [
    "modal_other_product", "modal_other_copy", "links_other_product",
    "links_other_copy", "wrong_query_product", "wrong_url_path",
    "other_origin", "unknown_query", "repeated_param", "missing_format",
    "other_format", "lcp", "two_direct_options", "foreign_card",
    "unrelated_readbook", "other_order_ref",
])
def test_unproven_direct_link_never_authorizes_signed_get(mutation):
    html = confirmation()
    target = SIGNED_EPUB
    if mutation == "modal_other_product":
        html = html.replace("ModalDownload(9001, 1)", "ModalDownload(9999, 1)")
    elif mutation == "modal_other_copy":
        html = html.replace("ModalDownload(9001, 1)", "ModalDownload(9001, 2)")
    elif mutation == "links_other_product":
        html = html.replace("ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'NOT_SPECIFIED'",
                            "ProductId: 9999, IndexExemplaire: 1, DrmTechnlology: 'NOT_SPECIFIED'")
    elif mutation == "links_other_copy":
        html = html.replace("IndexExemplaire: 1, DrmTechnlology: 'NOT_SPECIFIED'",
                            "IndexExemplaire: 2, DrmTechnlology: 'NOT_SPECIFIED'")
    elif mutation == "wrong_query_product":
        html = html.replace("productid=9001", "productid=9999")
    elif mutation == "wrong_url_path":
        html = html.replace("HandlerDilicomDownloadLink.ashx", "OtherDownload.ashx")
    elif mutation == "other_origin":
        html = html.replace("services.numilog.com", "untrusted.invalid")
    elif mutation == "unknown_query":
        html = html.replace("idcmd=42", "idcmd=42&extra=opaque")
    elif mutation == "repeated_param":
        html = html.replace("idcmd=42", "idcmd=42&idcmd=43")
    elif mutation == "missing_format":
        html = html.replace("format: 'EPUB', ", "")
    elif mutation == "other_format":
        html = html.replace("format: 'EPUB'", "format: 'PDF'")
    elif mutation == "lcp":
        html = html.replace("DrmTechnlology: 'NOT_SPECIFIED'",
                            "DrmTechnlology: 'LCP'")
    elif mutation == "two_direct_options":
        extra = (
            "{ ProductId: 9001, IndexExemplaire: 1, "
            "DrmTechnlology: 'NOT_SPECIFIED', format: 'EPUB', "
            "link: '" + SIGNED_EPUB + "' },"
        )
        html = html.replace("];", extra + "];", 1)
    elif mutation == "foreign_card":
        html = html.replace('<div class="card mp-4">',
                            '<div class="card mp-4"><div class="card">Second livre</div>',
                            1)
    elif mutation == "unrelated_readbook":
        html = html.replace(
            "</form>",
            '<a href="/Pages/Compte/readBook.ashx?lid=999&amp;m=stream">Other</a>'
            "</form>", 1,
        )
    else:
        html = html.replace("ref=FICTIONAL&amp;disp=simple",
                            "ref=UNRELATED&amp;disp=simple", 1)
    assert html != confirmation()
    with pytest.raises(DeliverySelectionError):
        delivery_url(
            html, LANDING, allowed_origins=ALLOW_EPUB,
            expected_readbook=READ_FOURTH, require_bound_confirmation=True,
            allow_missing_readbook=True,
        )


@pytest.mark.parametrize("failure", ["invalid_epub", "wrong_mime", "forbidden", "off_origin"])
def test_signed_direct_get_is_validated_and_never_calls_adept(tmp_path, failure):
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY)
        if url == READ_FOURTH:
            return httpx.Response(302, headers={"location": LANDING})
        if url == LANDING:
            return httpx.Response(200, text=confirmation(),
                                  headers={"content-type": "text/html"})
        assert url == SIGNED_EPUB
        if failure == "wrong_mime":
            return httpx.Response(200, content=fake_epub_bytes(),
                                  headers={"content-type": "application/pdf"})
        if failure == "forbidden":
            return httpx.Response(403)
        if failure == "off_origin":
            return httpx.Response(302, headers={"location": "https://evil.invalid/"})
        return httpx.Response(200, content=b"NOT_EPUB",
                              headers={"content-type": "application/epub+zip"})

    session, diagnostics, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        selected = next(x for x in session.library()
                        if x.title == "Achat fictif 78")
        with pytest.raises(ProviderError):
            session.download_direct(selected, tmp_path)
        assert requested == [LIBRARY, LIBRARY, READ_FOURTH, LANDING, SIGNED_EPUB]
        assert broker.calls == []
        assert not list(tmp_path.glob("*.epub"))
        assert completed_events(diagnostics)[0][0] == "error"
        assert "FAKE_SIGNATURE" not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()
