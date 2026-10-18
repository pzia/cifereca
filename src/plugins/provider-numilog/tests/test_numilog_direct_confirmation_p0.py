"""#2024: an exact owned-card confirmation href is optional, never guessed.

All order refs, cookies, titles and download assets here are synthetic.
There is no live Numilog request, browser import or real Adobe identity.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_html import Parser, _scoped_confirmation_href, parse_library_page
from tests.test_numilog_confirmation_structure_p0 import (
    ACSM, CONFIRMATION, FIXTURE, INVENTORY, LIBRARY, READ_12, SIGNED,
)
from tests.test_numilog_signed_acsm_transport_p0 import setup, _assert_clean

DIRECT = CONFIRMATION.replace('ref=FICTIONAL&disp=1', 'ref=PRIVATE_ORDER&disp=simple')
READ_CARD_ANCHOR = '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&amp;m=download">'
CONFIRMATION_ANCHOR = (
    '<a class="btn-choose-download" '
    'href="/Pages/Commande/ConfirmationPaiement.aspx?ref=PRIVATE_ORDER&amp;disp=simple">'
    'Je télécharge le livre</a>'
)
assert DIRECT != CONFIRMATION and READ_CARD_ANCHOR in INVENTORY


def inventory_with_first_card_link(anchor=CONFIRMATION_ANCHOR):
    complete = READ_CARD_ANCHOR + 'Télécharger</a>'
    return INVENTORY.replace(complete, complete + anchor, 1)


def confirmation_with_matching_synthetic_order():
    return FIXTURE.read_text().replace(
        'ref=FICTIONAL&amp;disp=1', 'ref=PRIVATE_ORDER&amp;disp=simple',
    )


@pytest.mark.parametrize('with_login_widget', (False, True))
def test_owned_direct_confirmation_href_selects_only_fresh_matching_acsm(
    tmp_path, with_login_widget,
):
    inventory = inventory_with_first_card_link()
    confirmed = confirmation_with_matching_synthetic_order()
    if with_login_widget:
        confirmed = confirmed.replace(
            '<form method="post"',
            '<input type="password" name="templateLogin" value="PRIVATE_HTML" />'
            '<form method="post"', 1,
        )
    requested = []

    def handler(request):
        requested.append(request)
        url = str(request.url)
        if url == LIBRARY:
            return httpx.Response(200, text=inventory,
                                  headers={'content-type': 'text/html'})
        if url == DIRECT:
            return httpx.Response(200, text=confirmed,
                                  headers={'content-type': 'text/html'})
        if url == SIGNED:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('Direct owned-card path must not invent a readBook hop')

    session, diag, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        # Ordinary library/search never materializes a private order href.
        listed = session.library()
        assert len(listed) == 2
        assert 'PRIVATE_ORDER' not in repr(listed)
        path, origin = session.download_direct(listed[0], tmp_path)
        assert path.is_file() and origin == LIBRARY
        assert [str(request.url) for request in requested] == [
            LIBRARY, LIBRARY, DIRECT, SIGNED,
        ]
        assert requested[2].headers.get('referer') == LIBRARY
        assert requested[3].headers.get('referer') == session.base_url
        assert len(importer.calls) == 1
        details = [
            kwargs['detail'] for args, kwargs in diag.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        ]
        assert [d['path_kind'] for d in details if 'path_kind' in d] == [
            'confirmation_page', 'acsm_fulfillment',
        ]
        assert details[-1]['code'] == 'acsm_complete'
        # Source names the route ACTUALLY selected at user click, not the
        # readBook evidence retained only for ownership/binding validation.
        assert details[-1]['source'] == 'owned_card'
        assert details[0]['phase'] == 'start'
        assert details[0]['source'] == 'owned_card'
        assert all(d['source'] == 'owned_card' for d in details)
        assert all(token not in repr(diag.events) for token in (
            'PRIVATE_ORDER', 'PRIVATE_HTML', 'FICTIONAL_LCP',
            'SYNTHETIC_NOT_A_CREDENTIAL', 'ref=', 'disp=',
        ))
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_order_href_from_other_book_is_not_selected(tmp_path):
    # Only book 34 bears the explicit order navigation. Book 12 may still
    # follow readBook, but cannot use book 34's private confirmation URL.
    inventory = INVENTORY.replace(
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=34&amp;m=download">Télécharger</a>',
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=34&amp;m=download">Télécharger</a>'
        + CONFIRMATION_ANCHOR,
        1,
    )
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if str(request.url) == LIBRARY:
            return httpx.Response(200, text=inventory)
        assert str(request.url) == READ_12, 'Other book order must not be consumed'
        return httpx.Response(403, text='PRIVATE_ACCOUNT')

    session, diag, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        item = session.library()[0]
        with pytest.raises(ProviderError):
            session.download_direct(item, tmp_path)
        assert requested == [LIBRARY, LIBRARY, READ_12]
        assert not importer.calls
        assert 'PRIVATE_ORDER' not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('bad_href', (
    '/Pages/Commande/ConfirmationPaiement.aspx?ref=&amp;disp=simple',
    '/Pages/Commande/ConfirmationPaiement.aspx?ref=PRIVATE_ORDER&amp;ref=OTHER&amp;disp=simple',
    '/Pages/Commande/ConfirmationPaiement.aspx?ref=PRIVATE_ORDER&amp;disp=1',
    '/Pages/Commande/ConfirmationPaiement.aspx?ref=PRIVATE_ORDER&amp;disp=simple&amp;extra=1',
    'https://evil.invalid/Pages/Commande/ConfirmationPaiement.aspx?ref=PRIVATE_ORDER&amp;disp=simple',
    '/Pages/Commande/ConfirmationPaiement.aspx?ref=PRIVATE_ORDER&amp;disp=simple#SECRET',
    '/Pages/Commande/../Commande/ConfirmationPaiement.aspx?ref=PRIVATE_ORDER&amp;disp=simple',
))
def test_unqualified_href_cannot_override_fresh_readbook(tmp_path, bad_href):
    # The selected card still has a valid readBook fallback.
    inventory = inventory_with_first_card_link(
        '<a href="' + bad_href + '">Unqualified</a>',
    )
    rows, _ = parse_library_page(
        inventory, delivery_evidence=True,
        delivery_selector=lambda row: row['lid'] == '12',
    )
    assert len(rows) == 2
    assert 'confirmation' not in rows[0]
    assert rows[0]['href'] == READ_12


def test_conflicting_confirmation_hrefs_fail_before_any_order_get(tmp_path):
    conflicting = CONFIRMATION_ANCHOR + CONFIRMATION_ANCHOR.replace(
        'PRIVATE_ORDER', 'ANOTHER_PRIVATE_ORDER',
    )
    inventory = inventory_with_first_card_link(conflicting)
    requested = []

    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == LIBRARY
        return httpx.Response(200, text=inventory)

    session, diag, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        item = session.library()[0]
        with pytest.raises(ProviderError, match='Liens de confirmation concurrents'):
            session.download_direct(item, tmp_path)
        assert requested == [LIBRARY, LIBRARY]
        assert not importer.calls
        assert 'PRIVATE_ORDER' not in repr(diag.events)
        assert 'ANOTHER_PRIVATE_ORDER' not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_multi_variant_owned_card_does_not_bind_one_confirmation_to_both_editions():
    html = (
        '<table id="myTable"><div><span class="pTitle">Same Title</span>'
        + READ_CARD_ANCHOR + 'First</a>'
        + '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=34&amp;m=download">Another</a>'
        + CONFIRMATION_ANCHOR
        + '</div></table>'
    )
    rows, _ = parse_library_page(
        html, delivery_evidence=True,
        delivery_selector=lambda row: row['lid'] == '12',
    )
    assert len(rows) == 2
    assert all('confirmation' not in row for row in rows)


def test_malformed_browser_anchor_is_skipped_without_serializing_private_ref():
    html = inventory_with_first_card_link(
        '<a href="/Pages/Commande/ConfirmationPaiement.aspx?ref=PRIVATE_ORDER&amp;disp=simple'
        + chr(10) + 'other=SECRET">Malformed</a>',
    )
    rows, _ = parse_library_page(
        html, delivery_evidence=True,
        delivery_selector=lambda row: row['lid'] == '12',
    )
    assert len(rows) == 2
    assert 'confirmation' not in rows[0]


def test_oversized_owned_confirmation_href_is_not_sent_or_saved():
    # A huge opaque ref is not a reason to retain it, attempt a GET, or
    # allow a malformed direct route to override the original readBook.
    huge_ref = 'PRIVATE_BUT_SYNTHETIC_' + 'X' * 4200
    inventory = inventory_with_first_card_link(
        '<a href="/Pages/Commande/ConfirmationPaiement.aspx?ref='
        + huge_ref + '&amp;disp=simple">Oversized</a>',
    )
    rows, _ = parse_library_page(
        inventory, delivery_evidence=True,
        delivery_selector=lambda row: row['lid'] == '12',
    )
    assert len(rows) == 2
    assert rows[0]['href'] == READ_12
    assert 'confirmation' not in rows[0]
    assert huge_ref not in repr(rows)


@pytest.mark.parametrize("with_login_widget", (False, True))
def test_unbound_owned_card_confirmation_keeps_true_source_on_failure(
    tmp_path, with_login_widget,
):
    """Even after rejecting HTML, the origin of this user action is public."""
    html = (
        '<input type="password" name="loginWidget" value="PRIVATE_HTML"/>'
        if with_login_widget else '<div>Unbound order</div>'
    )
    html += '<a href="' + SIGNED + '">Unrelated signed link</a>'
    requests = []

    def handler(request):
        url = str(request.url)
        requests.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=inventory_with_first_card_link())
        assert url == DIRECT, 'No readBook or unbound ACSM request is authorized'
        return httpx.Response(200, text=html,
                              headers={'content-type': 'text/html'})

    session, diag, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        book = session.library()[0]
        with pytest.raises(ProviderError):
            session.download_direct(book, tmp_path)
        assert requests == [LIBRARY, LIBRARY, DIRECT]
        assert session.connected is True and not importer.calls
        events = [
            kwargs['detail'] for args, kwargs in diag.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        ]
        assert [event['phase'] for event in events] == [
            'start', 'delivery_http', 'html_selection', 'completed',
        ]
        assert all(event['source'] == 'owned_card' for event in events)
        assert events[1]['path_kind'] == 'confirmation_page'
        assert events[-1]['code'] == (
            'login_form_detected' if with_login_widget
            else 'html_delivery_link_missing'
        )
        assert all(value not in repr(diag.events) for value in (
            'PRIVATE_HTML', 'PRIVATE_ORDER', 'ref=', 'disp=',
            'SYNTHETIC_NOT_A_CREDENTIAL',
        ))
        _assert_clean(tmp_path)
    finally:
        session.close()
