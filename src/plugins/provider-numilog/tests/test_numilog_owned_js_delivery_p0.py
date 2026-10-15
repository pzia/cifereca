"""P0 #1883 — fictional owned-card ACSM evidence, no real signed URLs/network.

The library-page versus readBook-page distinction is intentional: an
unscoped script never licenses a download for a different purchased book.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from cifereca.plugin_api import ProviderError, ProviderCapabilityError
from cifereca_numilog_html import (
    parse_library_page, delivery_url, DeliverySelectionError,
)
from tests.test_numilog_signed_acsm_transport_p0 import (
    setup, ACSM, SIGNED,
)

LIBRARY = "https://www.numilog.com/Pages/Compte/MesLivres.aspx"
READ_12 = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=12&m=download"
READ_34 = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=34&m=download"
SECOND = SIGNED.replace("FICTIONAL", "SECOND_FICTIONAL").replace(
    "SYNTHETIC_NOT_A_CREDENTIAL", "SECOND_NOT_A_CREDENTIAL",
)
assert SIGNED != SECOND


def _object(product, link, *, index=1, drm="ADOBE"):
    return (
        "{ProductId: " + str(product)
        + ", IndexExemplaire: " + str(index)
        + ", DrmTechnlology: '" + drm
        + "', link: '" + link + "'}"
    )


def _card(lid, product, link, *, index=1, attrs="", extra_script="", drm="ADOBE"):
    return (
        f'<div class="owned-book" data-product-id="{product}" '
        f'data-index-exemplaire="{index}" {attrs}>'
        f'<span class="pTitle">Achat fictif {lid}</span>'
        f'<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid={lid}&amp;m=download">'
        f'Télécharger</a>'
        f'<script>window.book = {_object(product, link, index=index, drm=drm)};</script>'
        f'{extra_script}'
        '</div>'
    )


def _page(*cards, outside_script=""):
    return (
        '<html><body><table id="myTable">'
        + ''.join(cards)
        + '</table>' + outside_script + '</body></html>'
    )


def _handler_for_library(html, *, failure=None):
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(
                200, text=html, headers={"content-type": "text/html"},
            )
        if url in {SIGNED, SECOND}:
            if failure == "403":
                return httpx.Response(403)
            if failure == "410":
                return httpx.Response(410)
            if failure == "429":
                return httpx.Response(429)
            if failure == "xml-non-acsm":
                return httpx.Response(
                    200, content=b"<other/>", headers={"content-type": "application/xml"},
                )
            if failure == "login":
                return httpx.Response(
                    200, text='<html><form><input type="password"></form></html>',
                    headers={"content-type": "text/html"},
                )
            if failure == "untrusted-redirect":
                return httpx.Response(
                    302, headers={"location": "https://evil.invalid/book.acsm"},
                )
            return httpx.Response(
                200, content=ACSM,
                headers={"content-type": "application/vnd.adobe.adept+xml"},
            )
        if url in {READ_12, READ_34}:
            if failure == "login":
                return httpx.Response(
                    200, text='<html><form><input type="password"></form></html>',
                    headers={"content-type": "text/html"},
                )
            if failure == "ambiguous":
                return httpx.Response(
                    200, text=f'<a href="{SIGNED}">A</a><a href="{SECOND}">B</a>',
                    headers={"content-type": "text/html"},
                )
            if failure == "error-html":
                return httpx.Response(
                    200, text='<html><h1>Erreur</h1></html>',
                    headers={"content-type": "text/html"},
                )
            return httpx.Response(
                200, text='<html><p>No delivery link</p></html>',
                headers={"content-type": "text/html"},
            )
        raise AssertionError("Unexpected outbound URL")

    return handler, requested


def _session(tmp_path, html, *, failure=None, status="complete"):
    handler, requested = _handler_for_library(html, failure=failure)
    session, diag, acquisition = setup(tmp_path, handler, status=status)
    session.connected = True  # already-authenticated fake account
    return session, diag, acquisition, requested


def _assert_no_stage(tmp_path):
    assert list(tmp_path.glob(".numilog-*")) == []
    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob("*.acsm")) == []


def test_card_delivery_is_inert_for_search_and_inventory_but_current_on_click(tmp_path):
    page = _page(
        _card(12, 9001, SIGNED),
        _card(34, 9002, SECOND),
        '<div class="not-owned"><span class="pTitle">Non acheté</span></div>',
    )
    session, diagnostics, acquisition, requested = _session(tmp_path, page)
    try:
        items = session.library()
        assert len(items) == 2
        assert requested == [LIBRARY]
        for item in items:
            assert item.owned and item.downloadable
            assert item.download_url is None and item.friendly_url is None
            assert "auth=" not in repr(item)
            assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(item)
        assert len(session.search("Achat fictif")) == 2
        assert requested == [LIBRARY, LIBRARY]  # search is inventory ONLY
        assert not acquisition.calls

        selected = items[1]  # product 9002 differs from lid 34
        completed, source = session.download_direct(selected, tmp_path)
        assert completed.is_file()
        assert source == LIBRARY
        assert requested == [LIBRARY, LIBRARY, LIBRARY, SECOND]
        assert SIGNED not in requested  # no borrowing book 12's ACSM
        assert READ_34 not in requested  # fresh attested card link is preferred
        assert len(acquisition.calls) == 1
        assert acquisition.calls[0][1]["identity_id"] == "fictional-reader"
        rendered = repr(diagnostics.events) + repr(selected) + repr(acquisition.calls[0][1])
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in rendered
        assert "SECOND_NOT_A_CREDENTIAL" not in rendered
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_download_of_other_owned_book_uses_only_its_own_signed_link(tmp_path):
    page = _page(_card(12, 9001, SIGNED), _card(34, 9002, SECOND))
    session, _, broker, requested = _session(tmp_path, page)
    try:
        item = next(x for x in session.library() if "12" in x.title)
        result, _ = session.download_direct(item, tmp_path)
        assert result.is_file()
        assert requested == [LIBRARY, LIBRARY, SIGNED]
        assert SECOND not in requested
        assert len(broker.calls) == 1
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_owned_cards_are_never_auto_downloaded_during_multiple_inventory_refreshes(tmp_path):
    session, _, broker, requested = _session(tmp_path, _page(
        _card(12, 9001, SIGNED), _card(34, 9002, SECOND),
    ))
    try:
        for _ in range(3):
            assert len(session.library()) == 2
        assert requested == [LIBRARY] * 3
        assert broker.calls == []
        assert not list(tmp_path.glob("*.epub"))
    finally:
        session.close()


@pytest.mark.parametrize("bad", [
    SIGNED.replace("https://", "http://", 1),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com.evil.invalid"),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com:8443"),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com:0"),
    SIGNED.replace("https://", "https://user:pass@", 1),
    SIGNED + "#fragment",
    "https://entrepot2r.numilog.com/fulfillment/other.acsm?fake=1",
])
def test_html_owned_object_refuses_untrusted_destinations_before_get(tmp_path, bad):
    page = _page(_card(12, 9001, bad))
    rows, _ = parse_library_page(page, delivery_evidence=True)
    assert len(rows) == 1 and "delivery" not in rows[0]
    session, _, broker, requested = _session(tmp_path, page)
    try:
        with pytest.raises(ProviderError, match="lien de livraison absent"):
            session.download_direct(session.library()[0], tmp_path)
        assert requested == [LIBRARY, LIBRARY, READ_12]
        assert broker.calls == []
        _assert_no_stage(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize("form", ["global", "two-objects", "different-product", "different-index", "not-adobe"])
def test_unbound_or_ambiguous_js_object_must_fall_back_to_checked_readbook(
    tmp_path, form,
):
    html = _page(_card(12, 9001, SIGNED))
    if form == "global":
        html = _page(
            '<div><a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&m=download">'
            'Télécharger</a></div>',
            outside_script=f'<script>window.uncertain={_object(9001, SIGNED)};</script>',
        )
    elif form == "two-objects":
        html = _page(_card(12, 9001, SIGNED,
                          extra_script=f'<script>window.second={_object(9001, SECOND)};</script>'))
    elif form == "different-product":
        html = _page(_card(12, 9001, SIGNED).replace('data-product-id="9001"',
                                                     'data-product-id="9007"'))
    elif form == "different-index":
        html = _page(_card(12, 9001, SIGNED).replace(
            'data-index-exemplaire="1"', 'data-index-exemplaire="3"'))
    else:
        html = _page(_card(12, 9001, SIGNED, drm="LCP"))
    rows, _ = parse_library_page(html, delivery_evidence=True)
    assert rows and all("delivery" not in row for row in rows)
    session, _, broker, requested = _session(tmp_path, html)
    try:
        with pytest.raises(ProviderError, match="lien de livraison absent"):
            session.download_direct(session.library()[0], tmp_path)
        assert requested == [LIBRARY, LIBRARY, READ_12]
        assert broker.calls == []
        _assert_no_stage(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize("failure", ["403", "410", "429", "xml-non-acsm", "login",
                                      "untrusted-redirect"])
def test_scoped_signed_failure_is_explicit_safe_and_leaves_no_temps(tmp_path, failure):
    session, diagnostics, broker, requested = _session(
        tmp_path, _page(_card(12, 9001, SIGNED)), failure=failure,
    )
    try:
        item = session.library()[0]
        with pytest.raises((ProviderError, ProviderCapabilityError)) as exc:
            session.download_direct(item, tmp_path)
        if failure == "410":
            assert "expiré" in str(exc.value)
        if failure == "login":
            assert "session" in str(exc.value)
        if failure == "xml-non-acsm":
            assert "XML non ACSM" in str(exc.value)
        assert requested == [LIBRARY, LIBRARY, SIGNED]
        assert broker.calls == []
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diagnostics.events)
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in str(exc.value)
        _assert_no_stage(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize(("failure", "part"), [
    ("login", "session de connexion expirée"),
    ("ambiguous", "plusieurs liens de livraison"),
    ("error-html", "page d’erreur"),
    (None, "lien de livraison absent"),
])
def test_readbook_200_html_errors_are_distinct_and_do_not_persist_secrets(
    tmp_path, failure, part,
):
    page = _page('<div><span class="pTitle">Achat 12</span>'
                 '<a class="LnkDownloadBook" '
                 'href="/Pages/Compte/readBook.ashx?lid=12&m=download">Télécharger</a></div>')
    session, diagnostics, broker, requested = _session(
        tmp_path, page, failure=failure,
    )
    try:
        item = session.library()[0]
        with pytest.raises(ProviderError, match=part):
            session.download_direct(item, tmp_path)
        assert requested == [LIBRARY, LIBRARY, READ_12]
        assert broker.calls == []
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diagnostics.events)
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_readbook_intermediate_js_object_already_supported_without_library_object(tmp_path):
    library = _page('<div><span class="pTitle">Owned</span>'
                    '<a class="LnkDownloadBook" '
                    'href="/Pages/Compte/readBook.ashx?lid=12&m=download">Download</a></div>')
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=library, headers={"content-type": "text/html"})
        if url == READ_12:
            return httpx.Response(
                200, text=f'<script>window.delivery={_object(9001, SIGNED)};</script>',
                headers={"content-type": "text/html"},
            )
        if url == SIGNED:
            return httpx.Response(
                200, content=ACSM,
                headers={"content-type": "application/vnd.adobe.adept+xml"},
            )
        raise AssertionError("No unlisted URL")

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        item = session.library()[0]
        local, stable_url = session.download_direct(item, tmp_path)
        assert local.is_file() and stable_url == LIBRARY
        assert requested == [LIBRARY, LIBRARY, READ_12, SIGNED]
        assert len(broker.calls) == 1
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diag.events)
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_adept_attention_required_propagates_only_fixed_public_code(tmp_path):
    session, diagnostics, broker, requested = _session(
        tmp_path, _page(_card(12, 9001, SIGNED)),
        status="attention_required",
    )
    try:
        item = session.library()[0]
        with pytest.raises(ProviderCapabilityError, match="adept_attention_required"):
            session.download_direct(item, tmp_path)
        assert requested == [LIBRARY, LIBRARY, SIGNED]
        assert len(broker.calls) == 1
        _assert_no_stage(tmp_path)
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(diagnostics.events)
    finally:
        session.close()


def test_unowned_reference_is_rejected_without_any_network_request(tmp_path):
    session, _, broker, requested = _session(tmp_path, _page(_card(12, 9001, SIGNED)))
    try:
        from cifereca.plugin_api import SearchResult
        unowned = SearchResult(
            id="numilog:bogus:999:fake", record_id="numilog:bogus:999:fake",
            docbase="NUMILOG", medium="digital", title="Not owned",
            author=None, type="ebook", format=None, publisher=None,
            published_at=None, description=None, thumbnail_url=None,
            source_provider="numilog", source_credential="other",
        )
        with pytest.raises(ProviderError, match="autre connexion"):
            session.download_direct(unowned, tmp_path)
        assert requested == []
        assert broker.calls == []
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_parser_invents_no_product_lid_equivalence_and_links_are_ephemeral():
    html = _page(_card(12, 8001, SIGNED, index=6))
    inventory, _ = parse_library_page(html)
    fresh, _ = parse_library_page(html, delivery_evidence=True)
    assert "delivery" not in inventory[0]
    assert fresh[0]["lid"] == "12"
    assert fresh[0]["delivery"].product_id == "8001"
    assert fresh[0]["delivery"].copy_index == "6"
    assert fresh[0]["delivery"].url == SIGNED
    assert delivery_url(
        f'<script>window.deliver={_object(8001, SIGNED, index=6)};</script>',
        READ_12, allowed_origins={("https", "entrepot2r.numilog.com", 443)},
    ) == SIGNED
    with pytest.raises(DeliverySelectionError) as exc:
        delivery_url('<html></html>', READ_12)
    assert exc.value.code == "missing"
