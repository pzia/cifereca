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



def test_unrecognized_signed_handler_in_owned_card_remains_attestable():
    """A new vendor handler must not require a plugin-domain/path update.

    The Links entry and its exact product/copy binding are what matter.
    Parsing an inventory never consumes that signed link.
    """
    new_handler = 'https://entrepot2r.numilog.com/fulfillment/other.acsm?fake=1'
    page = _page(_card(12, 9001, new_handler))
    rows, _ = parse_library_page(page, delivery_evidence=True)
    assert len(rows) == 1
    assert rows[0]['delivery'].url == new_handler
    assert rows[0]['delivery'].delivery_kind == 'acsm'


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
            assert "formulaire de connexion" in str(exc.value)
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
    ("login", "formulaire de connexion"),
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
            friendly_url=None, scenario="NUMILOG_LIBRARY",
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
    assert "SYNTHETIC_NOT_A_CREDENTIAL" not in repr(fresh[0])
    assert delivery_url(
        f'<script>window.deliver={_object(8001, SIGNED, index=6)};</script>',
        READ_12, allowed_origins={("https", "entrepot2r.numilog.com", 443)},
    ) == SIGNED
    with pytest.raises(DeliverySelectionError) as exc:
        delivery_url('<html></html>', READ_12)
    assert exc.value.code == "missing"


@pytest.mark.parametrize("identities", [(), ("first", "second")])
def test_owner_resolution_is_required_before_signed_acsm_get(tmp_path, identities):
    page = _page(_card(12, 9001, SIGNED))
    handler, requested = _handler_for_library(page)
    session, _, broker = setup(tmp_path, handler, identities=identities)
    session.connected = True
    try:
        book = session.library()[0]
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire"):
            session.download_direct(book, tmp_path)
        assert requested == [LIBRARY]  # refuse before the fresh private read
        assert broker.calls == []
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_missing_acsm_broker_blocks_direct_signed_get(tmp_path):
    session, _, broker, requested = _session(tmp_path, _page(_card(12, 9001, SIGNED)))
    session.acquisition_broker = None
    try:
        book = session.library()[0]
        with pytest.raises(ProviderCapabilityError, match="broker acquisition requis"):
            session.download_direct(book, tmp_path)
        assert requested == [LIBRARY, LIBRARY]
        assert broker.calls == []
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_tbody_table_rows_keep_purchases_and_nested_markers_separate():
    """Real HTML tbody/tr wrappers never alias the first title or signed link."""
    first = _card(12, 9001, SIGNED)
    second = _card(34, 9002, SECOND).replace(
        'data-index-exemplaire="1"', 'data-index-exemplaire="1"',
    )
    html = (
        '<html><table id="myTable"><tbody>'
        '<tr data-product-id="9001"><td>' + first + '</td></tr>'
        '<tr data-product-id="9002"><td>' + second + '</td></tr>'
        '</tbody></table></html>'
    )
    diag = {}
    rows, _ = parse_library_page(
        html, delivery_evidence=True, delivery_diagnostics=diag,
    )
    assert [(r['lid'], r['title']) for r in rows] == [
        ('12', 'Achat fictif 12'), ('34', 'Achat fictif 34'),
    ]
    assert [r['delivery'].url for r in rows] == [SIGNED, SECOND]
    assert diag == {
        'cards': 2, 'js_shapes_in_cards': 2,
        'js_shapes_outside_cards': 0, 'bound_links': 2,
    }


def test_two_books_in_one_table_row_can_be_separated_by_independent_cards():
    html = (
        '<table id="myTable"><tbody><tr><td>'
        + _card(12, 9001, SIGNED)
        + _card(34, 9002, SECOND)
        + '</td></tr></tbody></table>'
    )
    result, _ = parse_library_page(html, delivery_evidence=True)
    assert len(result) == 2
    assert [row['title'] for row in result] == [
        'Achat fictif 12', 'Achat fictif 34',
    ]
    assert [row['delivery'].url for row in result] == [SIGNED, SECOND]


def test_unpartitioned_multi_title_row_rejected_instead_of_misattribution():
    html = (
        '<table id="myTable"><tbody><tr>'
        '<span class="pTitle">First book</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12">Download</a>'
        '<span class="pTitle">Second book</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=34">Download</a>'
        '</tr></tbody></table>'
    )
    from cifereca_numilog_html import ParseError
    with pytest.raises(ParseError, match='ambiguës'):
        parse_library_page(html, delivery_evidence=True)
    with pytest.raises(ParseError, match='ambiguës'):
        parse_library_page(html)  # inventory must also reject mixed records


def test_same_title_multiple_format_actions_still_projects_both_variants():
    html = (
        '<table id="myTable"><tbody><tr>'
        '<span class="pTitle">The same book</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&amp;format=epub">EPUB</a>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&amp;format=pdf">PDF</a>'
        f'<script>window.book={_object(9001, SIGNED)};</script>'
        '</tr></tbody></table>'
    )
    rows, _ = parse_library_page(html, delivery_evidence=True)
    assert len(rows) == 2
    assert [r['title'] for r in rows] == ['The same book'] * 2
    assert all('delivery' not in r for r in rows)  # variant unknown


def test_structural_diagnostic_locates_unbound_global_script_without_private_data():
    raw_html = _page(
        '<div><span class="pTitle">Achat</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12">Download</a></div>',
        outside_script=f'<script>window.b={_object(9001, SIGNED)};</script>',
    )
    structure = {}
    rows, _ = parse_library_page(
        raw_html, delivery_evidence=True, delivery_diagnostics=structure,
    )
    assert rows and 'delivery' not in rows[0]
    assert structure == {
        'cards': 1, 'js_shapes_in_cards': 0,
        'js_shapes_outside_cards': 1, 'bound_links': 0,
    }
    assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(structure)
    inventory, _ = parse_library_page(raw_html)
    assert inventory == [{k: v for k, v in rows[0].items() if k != 'delivery'}]


def test_structural_diagnostics_do_not_contain_purchase_identifiers_or_tokens(tmp_path):
    html = _page(
        _card(12, 9001, SIGNED),
        _card(34, 9002, SECOND),
        outside_script=f'<script>window.global={_object(9999, SIGNED)};</script>',
    )
    session, diagnostic, broker, requested = _session(tmp_path, html)
    try:
        item = session.library()[0]
        result, _ = session.download_direct(item, tmp_path)
        assert result.is_file()
        rows = [value for value in diagnostic.events
                if value[0][1] == 'bibliothèque livraison']
        assert len(rows) == 1
        details = rows[0][1]['detail']
        assert details['page_kind'] == 'meslivres'
        assert details['js_shapes_in_cards'] == 2
        assert details['js_shapes_outside_cards'] == 1
        assert details['bound_links'] == 1  # selected record only, not every purchase
        assert all(type(v) in {str, int} for v in details.values())
        text = repr(diagnostic.events)
        for sensitive in (
            'SYNTHETIC_NOT_A_CREDENTIAL',
            'SECOND_NOT_A_CREDENTIAL', '9001', '9002', '9999',
            'orderid=', 'auth=', 'readBook.ashx?lid=',
        ):
            assert sensitive not in text
        assert requested == [LIBRARY, LIBRARY, SIGNED]
        assert len(broker.calls) == 1
    finally:
        session.close()


def test_readbook_diagnostic_reveals_only_http_structure(tmp_path):
    from cifereca_numilog_html import delivery_url
    evidence = {}
    html = (
        f'<script>window.delivery={_object(8001, SIGNED, index=2)};</script>'
    )
    assert delivery_url(
        html, READ_12,
        allowed_origins={('https', 'entrepot2r.numilog.com', 443)},
        evidence=evidence,
    ) == SIGNED
    assert evidence == {
        'js_delivery_shapes': 1, 'absolute_html_hrefs': 0,
        'absolute_script_literals': 1,  # only the absolute URL is counted
        'allowed_candidates': 1,
        'attested_acsm_candidates': 1,
    }
    assert 'SIGNATURE' not in repr(evidence)


@pytest.mark.parametrize("identities", [(), ("first", "second")])
@pytest.mark.parametrize("via_html", [False, True])
def test_legacy_readbook_signed_redirect_also_checks_owner_before_get(
    tmp_path, identities, via_html,
):
    library = _page(
        '<div><span class="pTitle">One owned ebook</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&m=download">'
        'Download</a></div>'
    )
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=library, headers={'content-type': 'text/html'})
        if url == READ_12:
            if via_html:
                return httpx.Response(
                    200, text=f'<script>window.delivery="{SIGNED}";</script>',
                    headers={'content-type': 'text/html'},
                )
            return httpx.Response(302, headers={'location': SIGNED})
        raise AssertionError('No signed GET is permitted for ambiguous ownership')

    session, diag, broker = setup(tmp_path, handler, identities=identities)
    session.connected = True
    try:
        book = session.library()[0]
        with pytest.raises(ProviderCapabilityError, match='identité propriétaire'):
            session.download_direct(book, tmp_path)
        assert requested == [LIBRARY]  # ownership is now checked before any delivery I/O
        assert broker.calls == []
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diag.events)
        _assert_no_stage(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('path', [
    '/fulfillment/Other.acsm',
    '/fulfillment/URLLink.acsm/../Other.acsm',
    '/admin/private',
])
def test_signed_origin_is_not_a_wildcard_path(tmp_path, path):
    from cifereca_numilog import checked_url
    malicious = 'https://entrepot2r.numilog.com' + path + '?auth=SYNTHETIC'
    with pytest.raises(ProviderError, match='destination réseau inattendue'):
        checked_url(malicious)
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if str(request.url) == READ_12:
            return httpx.Response(302, headers={'location': malicious})
        raise AssertionError('Invalid ACSM path must be rejected before GET')

    session, diag, broker = setup(tmp_path, handler)
    try:
        with pytest.raises(ProviderError, match='destination réseau inattendue'):
            session._download(READ_12, tmp_path / 'target.epub')
        assert requested == [READ_12]
        assert broker.calls == []
        assert 'SYNTHETIC' not in repr(diag.events)
    finally:
        session.close()


def test_private_signed_link_is_decoded_only_for_explicit_selected_record(
    tmp_path, monkeypatch,
):
    """No signed URL from another bought book is even parsed into a row."""
    import cifereca_numilog_html as html_parser

    current = _page(
        _card(12, 9001, SIGNED),
        _card(34, 9002, SECOND),
    )
    session, _, broker, seen = _session(tmp_path, current)
    called = []
    original = html_parser._card_delivery

    def spy(card, links, origins):
        called.append(tuple(str(n.attrs.get('href') or '') for n in links))
        return original(card, links, origins)

    monkeypatch.setattr(html_parser, '_card_delivery', spy)
    try:
        from cifereca.plugin_api import ProviderError
        with pytest.raises(ProviderError, match='livre ciblé'):
            session._rows(include_delivery=True)
        assert seen == []
        items = session.library()
        assert seen == [LIBRARY]
        assert len(items) == 2
        assert called == []
        selected = next(row for row in items if row.title.endswith('34'))
        fresh = session._rows(
            include_delivery=True, delivery_record_id=selected.record_id,
        )
        assert seen == [LIBRARY, LIBRARY]
        assert len(fresh) == 2
        assert 'delivery' not in fresh[0]
        assert fresh[1]['delivery'].url == SECOND
        assert len(called) == 1 and 'lid=34' in called[0][0]
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(fresh)
        assert 'SECOND_NOT_A_CREDENTIAL' not in repr(fresh)
        assert not broker.calls
    finally:
        session.close()


def test_parser_selector_does_not_decode_other_books_and_preserves_inventory():
    from cifereca_numilog_html import parse_library_page
    page = _page(_card(12, 9001, SIGNED), _card(34, 9002, SECOND))
    inventory, _ = parse_library_page(page)
    matched, _ = parse_library_page(
        page,
        delivery_evidence=True,
        delivery_selector=lambda row: row['lid'] == '34',
    )
    assert len(matched) == len(inventory) == 2
    assert [
        {key: value for key, value in row.items() if key != 'delivery'}
        for row in matched
    ] == inventory
    assert 'delivery' not in matched[0]
    assert matched[1]['delivery'].url == SECOND
    assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(matched)
    assert 'SECOND_NOT_A_CREDENTIAL' not in repr(matched)


def test_duplicate_same_lid_and_format_on_one_page_never_consumes_signed_link(
    tmp_path,
):
    """Conflicting ProductId links with same readBook identity are ambiguous."""
    from cifereca_numilog_html import ParseError
    ambiguous = _page(_card(12, 9001, SIGNED), _card(12, 9002, SECOND))
    visible, _ = parse_library_page(ambiguous)
    assert len(visible) == 1  # legacy inventory still deduplicates
    with pytest.raises(ParseError, match='dupliquées'):
        parse_library_page(ambiguous, delivery_evidence=True)

    session, diag, broker, requested = _session(tmp_path, ambiguous)
    try:
        owned = session.library()[0]
        with pytest.raises(ProviderError, match='dupliquées'):
            session.download_direct(owned, tmp_path)
        assert requested == [LIBRARY, LIBRARY]
        assert broker.calls == []
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diag.events)
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_duplicate_book_identity_across_pagination_blocks_even_own_signed_get(
    tmp_path,
):
    """Two purchases with one stable lid/format never authorize first-wins."""
    second_page = LIBRARY + '?page=2'
    first = _page(_card(12, 9001, SIGNED)).replace(
        '</body></html>',
        '<a rel="next" href="/Pages/Compte/MesLivres.aspx?page=2">Next</a></body></html>',
    )
    other = _page(_card(12, 9002, SECOND))
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=first,
                                  headers={'content-type': 'text/html'})
        if url == second_page:
            return httpx.Response(200, text=other,
                                  headers={'content-type': 'text/html'})
        pytest.fail('Duplicate product must not reach either signed ACSM GET')

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        inventory = session.library()
        assert len(inventory) == 1
        with pytest.raises(ProviderError, match='dupliqué entre pages'):
            session.download_direct(inventory[0], tmp_path)
        assert requested == [LIBRARY, second_page, LIBRARY, second_page]
        assert broker.calls == []
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diag.events)
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_quoted_json_js_keys_produce_same_scoped_acsm_evidence(tmp_path):
    """Numilog can serialize the object as JSON rather than unquoted JS."""
    import json
    from cifereca_numilog_html import _js_delivery_shape_count, Parser
    obj = json.dumps({
        'ProductId': 9001, 'IndexExemplaire': 1,
        'DrmTechnlology': 'ADOBE', 'link': SIGNED,
    })
    html = _page(_card(12, 9001, SIGNED).replace(_object(9001, SIGNED), obj))
    scripts = [n for n in Parser(html).root.walk() if n.tag == 'script']
    assert sum(_js_delivery_shape_count(n) for n in scripts) == 1
    diag = {}
    rows, _ = parse_library_page(
        html, delivery_evidence=True, delivery_diagnostics=diag,
    )
    assert len(rows) == 1 and rows[0]['delivery'].url == SIGNED
    assert diag['js_shapes_in_cards'] == diag['bound_links'] == 1
    assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(rows)

    session, _, broker, requested = _session(tmp_path, html)
    try:
        book = session.library()[0]
        result, _ = session.download_direct(book, tmp_path)
        assert result.is_file()
        assert requested == [LIBRARY, LIBRARY, SIGNED]
        assert len(broker.calls) == 1
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_one_action_with_two_titles_refuses_acquisition_instead_of_wrong_book(
    tmp_path,
):
    from cifereca_numilog_html import ParseError
    ambiguous = _page(
        '<div><span class="pTitle">Bought book A</span>'
        '<span class="pTitle">Bought book B</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&m=download">Download</a>'
        f'<script>window.book={_object(9001, SIGNED)};</script></div>',
    )
    legacy, _ = parse_library_page(ambiguous)
    assert len(legacy) == 1  # inventory backward-compatible
    with pytest.raises(ParseError, match='Titres ambigus'):
        parse_library_page(ambiguous, delivery_evidence=True)
    session, _, broker, requested = _session(tmp_path, ambiguous)
    try:
        with pytest.raises(ProviderError, match='Titres ambigus'):
            session.download_direct(session.library()[0], tmp_path)
        assert requested == [LIBRARY, LIBRARY]
        assert broker.calls == []
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_orphan_card_without_title_does_not_license_js_signed_get(tmp_path):
    page = _page(
        '<div><a class="LnkDownloadBook" '
        'href="/Pages/Compte/readBook.ashx?lid=12&m=download">Download</a>'
        f'<script>window.book={_object(9001, SIGNED)};</script></div>',
    )
    rows, _ = parse_library_page(page, delivery_evidence=True)
    assert len(rows) == 1
    assert 'delivery' not in rows[0]  # title/copy provenance not attested
    session, _, broker, seen = _session(tmp_path, page)
    try:
        with pytest.raises(ProviderError, match='lien de livraison absent'):
            session.download_direct(session.library()[0], tmp_path)
        assert seen == [LIBRARY, LIBRARY, READ_12]
        assert broker.calls == []
        _assert_no_stage(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('across_pages', [False, True])
def test_unrelated_duplicate_does_not_prevent_unique_selected_book(
    tmp_path, across_pages,
):
    """Only a duplicate of the selected record is blocking."""
    second_page = LIBRARY + '?page=2'
    first_page = _page(
        _card(12, 9001, SIGNED), _card(34, 9002, SECOND),
        *([] if across_pages else [_card(12, 9007, SIGNED)]),
    )
    if across_pages:
        first_page = first_page.replace(
            '</body></html>',
            '<a rel="next" href="/Pages/Compte/MesLivres.aspx?page=2">'
            'Next</a></body></html>',
        )
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=first_page,
                                  headers={'content-type': 'text/html'})
        if across_pages and url == second_page:
            return httpx.Response(200, text=_page(_card(12, 9007, SIGNED)),
                                  headers={'content-type': 'text/html'})
        if url == SECOND:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('Unexpected URL or attempt to consume other book signature')

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        all_items = session.library()
        assert len(all_items) == 2
        chosen = next(x for x in all_items if x.title.endswith('34'))
        produced, _ = session.download_direct(chosen, tmp_path)
        assert produced.is_file()
        prefix = [LIBRARY, second_page] if across_pages else [LIBRARY]
        assert requested == prefix + prefix + [SECOND]
        assert SIGNED not in requested
        assert len(broker.calls) == 1
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diag.events)
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_single_signed_acsm_wins_over_allowed_site_navigation_in_html():
    """A page may include links to the storefront AND one real ACSM token."""
    navigation = 'https://www.numilog.com/Pages/Compte/MesLivres.aspx'
    another = 'https://services.numilog.com/common.css'
    html = (
        f'<a href="{navigation}">Mes achats</a>'
        f'<script>const appRoot = "{another}";'
        f'window.delivery = {_object(9001, SIGNED)};</script>'
    )
    allowed = {
        ('https', 'entrepot2r.numilog.com', 443),
        ('https', 'www.numilog.com', 443),
        ('https', 'services.numilog.com', 443),
    }
    diagnosis = {}
    selected = delivery_url(
        html, READ_12, allowed_origins=allowed, evidence=diagnosis,
    )
    assert selected == SIGNED
    assert diagnosis == {
        'js_delivery_shapes': 1,
        'absolute_html_hrefs': 1,
        'absolute_script_literals': 2,
        'allowed_candidates': 3,
        'attested_acsm_candidates': 1,
    }
    assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diagnosis)
    assert '9001' not in repr(diagnosis)


def test_two_distinct_signed_acsm_tokens_still_block_even_with_navigation():
    """Do not silently prefer the first signed order token for a purchase."""
    allowed = {
        ('https', 'entrepot2r.numilog.com', 443),
        ('https', 'www.numilog.com', 443),
    }
    html = (
        '<a href="https://www.numilog.com/">Accueil</a>'
        f'<script>const a = "{SIGNED}"; const b = "{SECOND}";</script>'
    )
    summary = {}
    with pytest.raises(DeliverySelectionError) as error:
        delivery_url(html, READ_12, allowed_origins=allowed, evidence=summary)
    assert error.value.code == 'ambiguous'
    assert summary['attested_acsm_candidates'] == 2
    assert summary['allowed_candidates'] == 3
    assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(summary)
    assert 'SECOND_NOT_A_CREDENTIAL' not in repr(summary)


def test_one_signed_acsm_among_html_links_is_fetched_once_via_real_broker(tmp_path):
    """READBook HTML with incidental allowed links must not cause a false P0 error."""
    visited = []
    nav = 'https://www.numilog.com/Pages/Compte/MesLivres.aspx'

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == READ_12:
            html = (
                f'<a href="{nav}">Ma bibliothèque</a>'
                f'<script>window.purchase={_object(9001, SIGNED)};</script>'
            )
            return httpx.Response(
                200, text=html,
                headers={'content-type': 'text/html; charset=utf-8'},
            )
        if url == SIGNED:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('Incidental navigation URL must not be requested')

    session, diagnostic, broker = setup(tmp_path, handler)
    try:
        published, stable_origin = session._download(
            READ_12, tmp_path / 'book.epub', title='Livre acheté',
        )
        assert published.is_file()
        assert stable_origin == LIBRARY
        assert visited == [READ_12, SIGNED]
        assert len(broker.calls) == 1
        assert broker.calls[0][1]['identity_id'] == 'fictional-reader'
        entries = [e for e in diagnostic.events if e[0][1] == 'livraison HTML']
        assert len(entries) == 1
        assert entries[0][1]['detail']['attested_acsm_candidates'] == 1
        assert entries[0][1]['detail']['allowed_candidates'] == 2
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diagnostic.events)
        _assert_no_stage(tmp_path)
    finally:
        session.close()


def test_html_without_any_signed_acsm_keeps_unambiguous_generic_delivery_path():
    """Non-ACSM direct EPUB delivery on loopback retains historical behavior."""
    from cifereca_numilog_html import origin
    endpoint = 'http://127.0.0.1:8094/Services/Delivery.ashx?lid=12'
    evidence = {}
    assert delivery_url(
        f'<a href="{endpoint}">EPUB</a>',
        'http://127.0.0.1:8094/Pages/Compte/readBook.ashx?lid=12',
        allowed_origins={origin('http://127.0.0.1:8094/')},
        evidence=evidence,
    ) == endpoint
    assert evidence['allowed_candidates'] == 1
    assert evidence['attested_acsm_candidates'] == 0
