"""#1883: actual page relationships, wholly fictional data and MockTransport.

The private attachment was inspected offline, never copied into this fixture.
Only structure is retained: global LCP/ADOBE alternatives for one product/copy,
a ModalDownload button, a distinct readBook lid and ordinary site navigation.
This tests HTTP-client/importer boundaries, not a live merchant or real DRM.
"""
from pathlib import Path
import http.cookiejar

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_html import (
    DeliverySelectionError, Parser, _owned_delivery_candidates,
    delivery_url, html_response_kind,
)
from tests.test_numilog_owned_js_delivery_p0 import (
    LIBRARY, READ_12, SECOND, _object,
)
from tests.test_numilog_signed_acsm_transport_p0 import (
    ACSM, SIGNED, setup, _assert_clean,
)

FIXTURE = Path(__file__).with_name('fixtures') / 'confirmation_delivery_structure.html'
CONFIRMATION = 'https://www.numilog.com/Pages/Commande/ConfirmationPaiement.aspx?ref=FICTIONAL&disp=1'
ALLOWED = {('https', 'www.numilog.com', 443), ('https', 'entrepot2r.numilog.com', 443)}
INVENTORY = '''<table id="myTable">
<div><span class="pTitle">Achat fictif 12</span>
<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&amp;m=download">Télécharger</a></div>
<div><span class="pTitle">Achat fictif 34</span>
<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=34&amp;m=download">Télécharger</a></div>
</table>'''


def test_global_lcp_and_adobe_for_same_copy_select_only_unique_allowed_acsm():
    html = FIXTURE.read_text()
    root = Parser(html).root
    assert not any(n.attrs.get('id') == 'myTable' for n in root.walk())
    assert html_response_kind(html) == 'intermediate'
    evidence = {}
    assert delivery_url(html, CONFIRMATION, allowed_origins=ALLOWED, evidence=evidence, expected_readbook=READ_12) == SIGNED
    assert evidence['js_delivery_shapes'] == 2
    # Prior parser rejected this ambiguity instead of identifying the ACSM.
    assert evidence['allowed_candidates'] == 2
    assert evidence['attested_acsm_candidates'] == 1
    candidates = _owned_delivery_candidates(root, allowed_origins=ALLOWED)
    assert len(candidates) == 1
    assert (candidates[0].product_id, candidates[0].copy_index) == ('9001', '1')
    assert 'ModalDownload(9001, 1)' in html
    assert candidates[0].url == SIGNED
    assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(evidence)


@pytest.mark.parametrize('disp', ('1', 'simple'))
def test_fresh_owned_readbook_redirect_confirmation_imports_one_acsm(tmp_path, disp):
    landing = CONFIRMATION.replace("disp=1", "disp=" + disp)
    requested = []
    html = FIXTURE.read_text().replace('disp=1', 'disp=' + disp)

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY, headers={'content-type': 'text/html'})
        if url == READ_12:
            return httpx.Response(302, headers={'location': landing})
        if url == landing:
            return httpx.Response(200, text=html, headers={'content-type': 'text/html'})
        if url == SIGNED:
            return httpx.Response(200, content=ACSM, headers={'content-type': 'application/vnd.adobe.adept+xml'})
        pytest.fail('No LCP, navigation, other purchase or unowned request is permitted')

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        books = session.library()
        assert len(books) == 2 and requested == [LIBRARY] and not importer.calls
        assert all(book.download_url is None for book in books)
        path, origin = session.download_direct(books[0], tmp_path)
        assert path.is_file() and origin == LIBRARY
        assert requested == [LIBRARY, LIBRARY, READ_12, landing, SIGNED]
        assert len(importer.calls) == 1
        # No signed ref or display value is echoed; route and query shape
        # are fixed enums/booleans on BOTH the 302 and confirmation 200.
        delivery_http = [
            kw['detail'] for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
            and kw['detail'].get('phase') == 'delivery_http'
        ]
        assert [row['path_kind'] for row in delivery_http] == [
            'readbook', 'confirmation_page', 'acsm_fulfillment',
        ]
        # This entry really sent a GET readBook before the 302. It is not
        # a direct owned-card href; Core can distinguish both using its
        # existing fixed and private-safe source enum.
        assert len({
            kw['detail']['source']
            for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        }) == 1
        assert all(
            kw['detail']['source'] == 'readbook'
            for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        )
        assert delivery_http[0]['redirect_target_kind'] == 'confirmation_page'
        assert delivery_http[0]['redirect_confirmation_ref_present'] is True
        assert delivery_http[0]['redirect_confirmation_disp_simple'] is (disp == 'simple')
        assert delivery_http[1]['confirmation_ref_present'] is True
        assert delivery_http[1]['confirmation_disp_simple'] is (disp == 'simple')
        assert 'ref=' not in repr(diagnostics.events)
        assert 'disp=' not in repr(diagnostics.events)
        assert importer.calls[0][1]['identity_id'] == 'fictional-reader'
        assert all(marker not in repr(diagnostics.events) for marker in (
            'SYNTHETIC_NOT_A_CREDENTIAL', 'FICTIONAL_LCP', 'orderid=', 'auth=',
        ))
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_second_signed_adobe_candidate_remains_ambiguous_before_any_import(tmp_path):
    html = FIXTURE.read_text().replace(
        '</script>', 'const conflict = ' + _object(9002, SECOND) + ';</script>',
    )
    with pytest.raises(DeliverySelectionError) as error:
        delivery_url(html, CONFIRMATION, allowed_origins=ALLOWED, expected_readbook=READ_12)
    assert error.value.code == 'ambiguous'
    requested = []

    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == CONFIRMATION
        return httpx.Response(200, text=html, headers={'content-type': 'text/html'})

    session, _diagnostics, importer = setup(tmp_path, handler)
    try:
        with pytest.raises(ProviderError, match='plusieurs liens'):
            session._download(CONFIRMATION, tmp_path / 'book.epub')
        assert requested == [CONFIRMATION] and not importer.calls
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('mutation', (
    'other-product', 'other-copy', 'other-readbook', 'other-format', 'other-query', 'normalized-readbook',
    'no-card', 'duplicate-card', 'dynamic-array', 'duplicate-array',
))
def test_confirmation_requires_exact_modal_copy_and_fresh_readbook_before_signed_get(tmp_path, mutation):
    html = FIXTURE.read_text()
    if mutation == 'other-product':
        html = html.replace('ModalDownload(9001, 1)', 'ModalDownload(9002, 1)')
    elif mutation == 'other-copy':
        html = html.replace('ModalDownload(9001, 1)', 'ModalDownload(9001, 2)')
    elif mutation == 'other-readbook':
        html = html.replace('lid=12', 'lid=34')
    elif mutation == 'other-format':
        html = html.replace('m=download', 'm=another-format')
    elif mutation == 'normalized-readbook':
        html = html.replace('readBook.ashx', 'read&#10;Book.ashx')
    elif mutation == 'other-query':
        html = html.replace('m=download', 'm=download&amp;copy=2')
    elif mutation == 'no-card':
        html = html.replace('class="card mp-4"', 'class="unknown"')
    elif mutation == 'duplicate-card':
        start = html.index('<div class="card mp-4">')
        end = html.index('<script')
        html = html[:end] + html[start:end] + html[end:]
    elif mutation == 'dynamic-array':
        html = html.replace('const Links = [', 'const Links = compute([', 1)
    else:
        html = html.replace('</script>', 'const Links = [];</script>')
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY, headers={'content-type': 'text/html'})
        if url == READ_12:
            return httpx.Response(302, headers={'location': CONFIRMATION})
        assert url == CONFIRMATION, 'Rejected binding must not consume a signed link'
        return httpx.Response(200, text=html, headers={'content-type': 'text/html'})

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        book = session.library()[0]
        with pytest.raises(ProviderError, match='plusieurs liens'):
            session.download_direct(book, tmp_path)
        assert requested == [LIBRARY, LIBRARY, READ_12, CONFIRMATION]
        assert not importer.calls
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('disp', ('1', 'simple'))
def test_shared_login_widget_does_not_block_exact_owned_confirmation_acsm(tmp_path, disp):
    """A site-wide login widget is not itself evidence of expired access.

    Only the already qualified confirmation card+ModalDownload+fresh readBook
    may select a signed ACSM, with no change to account authority.
    """
    landing = CONFIRMATION.replace("disp=1", "disp=" + disp)
    visited = []
    confirmation = FIXTURE.read_text().replace('disp=1', 'disp=' + disp).replace(
        '<form method="post"', '<input type="password" name="loginWidget" value="PRIVATE_HTML" />'
        '<form method="post"', 1,
    )
    assert html_response_kind(confirmation) == 'login'

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY,
                                  headers={'content-type': 'text/html'})
        if url == READ_12:
            return httpx.Response(302, headers={'location': landing})
        if url == landing:
            return httpx.Response(200, text=confirmation,
                                  headers={'content-type': 'text/html'})
        if url == SIGNED:
            return httpx.Response(200, content=ACSM, headers={
                'content-type': 'application/vnd.adobe.adept+xml',
            })
        pytest.fail('Unexpected network request on bound confirmation flow')

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        item = session.library()[0]
        path, origin = session.download_direct(item, tmp_path)
        assert path.is_file() and origin == LIBRARY
        assert visited == [LIBRARY, LIBRARY, READ_12, landing, SIGNED]
        assert session.connected is True and len(importer.calls) == 1
        # No signed ref or display value is echoed; route and query shape
        # are fixed enums/booleans on BOTH the 302 and confirmation 200.
        delivery_http = [
            kw['detail'] for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
            and kw['detail'].get('phase') == 'delivery_http'
        ]
        assert [row['path_kind'] for row in delivery_http] == [
            'readbook', 'confirmation_page', 'acsm_fulfillment',
        ]
        # This entry really sent a GET readBook before the 302. It is not
        # a direct owned-card href; Core can distinguish both using its
        # existing fixed and private-safe source enum.
        assert len({
            kw['detail']['source']
            for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        }) == 1
        assert all(
            kw['detail']['source'] == 'readbook'
            for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        )
        assert delivery_http[0]['redirect_target_kind'] == 'confirmation_page'
        assert delivery_http[0]['redirect_confirmation_ref_present'] is True
        assert delivery_http[0]['redirect_confirmation_disp_simple'] is (disp == 'simple')
        assert delivery_http[1]['confirmation_ref_present'] is True
        assert delivery_http[1]['confirmation_disp_simple'] is (disp == 'simple')
        assert 'ref=' not in repr(diagnostics.events)
        assert 'disp=' not in repr(diagnostics.events)
        assert diagnostics.events
        assert all(marker not in repr(diagnostics.events) for marker in (
            'PRIVATE_HTML', 'FICTIONAL_LCP', 'SYNTHETIC_NOT_A_CREDENTIAL',
            'orderid=', 'rights=', 'auth=',
        ))
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('mutation', (
    'different_product', 'different_copy', 'different_readbook',
    'missing_modal', 'duplicate_signed', 'missing_links_array', 'unqualified_landing',
))
def test_shared_login_widget_confirmation_refuses_unbound_acsm(tmp_path, mutation):
    """A login form plus ONE plausible signed link is never enough."""
    from tests.test_numilog_owned_js_delivery_p0 import SECOND

    html = FIXTURE.read_text().replace(
        '<form method="post"', '<input type="password" name="loginWidget" />'
        '<form method="post"', 1,
    )
    if mutation == 'different_product':
        html = html.replace('ModalDownload(9001, 1)', 'ModalDownload(9002, 1)')
    elif mutation == 'different_copy':
        html = html.replace('ModalDownload(9001, 1)', 'ModalDownload(9001, 2)')
    elif mutation == 'different_readbook':
        html = html.replace('lid=12', 'lid=34')
    elif mutation == 'missing_modal':
        html = html.replace('ModalDownload(9001, 1)', 'UnrelatedAction(9001, 1)')
    elif mutation == 'duplicate_signed':
        html = html.replace('</body>', '<script>const extra = "' + SECOND + '";</script></body>')
    elif mutation == 'missing_links_array':
        html = html.replace('const Links = [', 'const OtherLinks = [')
    landing = (
        'https://www.numilog.com/Pages/Compte/Landing.aspx'
        if mutation == 'unqualified_landing' else CONFIRMATION
    )
    visited = []

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY,
                                  headers={'content-type': 'text/html'})
        if url == READ_12:
            return httpx.Response(302, headers={'location': landing})
        assert url == landing, 'Unbound or hostile signed URL must never be consumed'
        return httpx.Response(200, text=html,
                              headers={'content-type': 'text/html'})

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        item = session.library()[0]
        with pytest.raises(ProviderError, match='session non invalidée'):
            session.download_direct(item, tmp_path)
        assert visited == [LIBRARY, LIBRARY, READ_12, landing]
        assert not importer.calls
        assert session.connected is True
        assert diagnostics.events
        # The failure has one bounded, discriminating html_selection event.
        # The terminal public code remains login_form_detected because a
        # password widget alone never proves that the cookie expired.
        selections = [
            kw['detail'] for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
            and kw['detail'].get('phase') == 'html_selection'
        ]
        expected_selection = (
            'login_form_detected' if mutation == 'unqualified_landing'
            else 'html_delivery_link_missing' if mutation == 'missing_links_array'
            else 'html_delivery_ambiguous'
        )
        assert len(selections) == 1
        assert selections[0]['code'] == expected_selection
        assert selections[0]['html_kind'] == 'login'
        assert selections[0].get('candidates', 0) <= 10000
        terminal = [
            kw['detail'] for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
            and kw['detail'].get('phase') == 'completed'
        ]
        assert len(terminal) == 1
        assert terminal[0]['code'] == 'login_form_detected'
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diagnostics.events)
        assert diagnostics.events[-1]
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_actual_login_endpoint_refuses_even_a_bound_confirmation_layout(tmp_path):
    """No exception to the exact auth endpoint, even if HTML mimics a card."""
    login = 'https://www.numilog.com/Login.Aspx'
    confirmation = FIXTURE.read_text().replace(
        '<form method="post"', '<input type="password" name="loginWidget" />'
        '<form method="post"', 1,
    )
    visited = []

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY)
        if url == READ_12:
            return httpx.Response(302, headers={'location': login})
        assert url == login
        return httpx.Response(200, text=confirmation,
                              headers={'content-type': 'text/html'})

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        item = session.library()[0]
        with pytest.raises(ProviderError, match='reconnecter le compte'):
            session.download_direct(item, tmp_path)
        assert visited == [LIBRARY, LIBRARY, READ_12, login]
        assert session.connected is False
        assert not importer.calls
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('html,error_label,error_code', (
    ('<a class="download" href="' + SIGNED + '">Télécharger</a>',
     'lien de livraison absent', 'html_delivery_link_missing'),
    ('<script>window.location = "' + SIGNED + '";</script>',
     'lien de livraison absent', 'html_delivery_link_missing'),
    ('<script>const Links = [{ProductId: 9001, IndexExemplaire: 1, '
     + 'DrmTechnlology: "ADOBE", link: "' + SIGNED + '"}];</script>',
     'plusieurs liens', 'html_delivery_ambiguous'),
))
def test_confirmation_without_login_never_consumes_unbound_signed_acsm(
    tmp_path, html, error_label, error_code,
):
    """Even a harmless template can advertise another order's signed URL.

    The confirmation route is ALWAYS scoped to the exact current purchase,
    not to whichever ACSM happens to be the only eligible link on the page.
    """
    visited = []
    landing = CONFIRMATION.replace('disp=1', 'disp=simple')

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY,
                                  headers={'content-type': 'text/html'})
        if url == READ_12:
            return httpx.Response(302, headers={'location': landing})
        assert url == landing, 'An unbound ACSM must never be fetched'
        return httpx.Response(200, text=html,
                              headers={'content-type': 'text/html'})

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        book = session.library()[0]
        with pytest.raises(ProviderError, match=error_label):
            session.download_direct(book, tmp_path)
        assert visited == [LIBRARY, LIBRARY, READ_12, landing]
        assert session.connected is True
        assert not importer.calls
        events = [
            kwargs['detail'] for args, kwargs in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        ]
        assert any(event.get('code') == error_code for event in events)
        assert events[-1]['code'] == error_code
        assert 'FICTIONAL' not in repr(diagnostics.events)
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_legacy_intermediate_without_login_still_allows_one_safe_signed_url(tmp_path):
    """Do not rewrite the historical non-confirmation readBook flow."""
    landing = 'https://www.numilog.com/Pages/Compte/Landing.aspx'
    visited = []

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY)
        if url == READ_12:
            return httpx.Response(302, headers={'location': landing})
        if url == landing:
            return httpx.Response(
                200, text='<script>window.location = "' + SIGNED + '";</script>',
                headers={'content-type': 'text/html'},
            )
        if url == SIGNED:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('Unexpected URL in legacy intermediate path')

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        book = session.library()[0]
        path, source = session.download_direct(book, tmp_path)
        assert path.is_file() and source == LIBRARY
        assert visited == [LIBRARY, LIBRARY, READ_12, landing, SIGNED]
        assert len(importer.calls) == 1
        assert 'SYNTHETIC_NOT_A_CREDENTIAL' not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('cookie_path,readbook_has_cookie,confirmation_has_cookie', (
    ('/', True, True),
    ('/Pages/Compte', True, False),
    ('/Pages/Commande', False, True),
    ('/Pages/Other', False, False),
))
def test_fictional_secure_cookie_path_provenance_across_confirmation(
    tmp_path, cookie_path, readbook_has_cookie, confirmation_has_cookie,
):
    """A Cookie header at readBook never proves its presence on confirmation.

    SameSite belongs to a browser navigation context; HTTPX keeps the
    attribute but does not reproduce the browser's SameSite enforcement.
    This test only proves Domain/Path/Secure eligibility, using a fake value.
    """
    requests = []
    landing = CONFIRMATION.replace('disp=1', 'disp=simple')

    def handler(request):
        requests.append(request)
        url = str(request.url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY)
        if url == READ_12:
            return httpx.Response(302, headers={'location': landing})
        if url == landing:
            return httpx.Response(200, text=FIXTURE.read_text(),
                                  headers={'content-type': 'text/html'})
        if url == SIGNED:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('Only the explicitly selected fictional order is allowed')

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    session.client.cookies.jar.set_cookie(http.cookiejar.Cookie(
        0, 'synthetic', 'PRIVATE_COOKIE', None, False,
        'www.numilog.com', False, False,
        cookie_path, True, True, None, True, None, None,
        {'SameSite': 'Lax'}, False,
    ))
    try:
        book = session.library()[0]
        path, source = session.download_direct(book, tmp_path)
        assert path.is_file() and source == LIBRARY
        assert len(importer.calls) == 1
        actual = {str(request.url): request for request in requests}
        assert ('synthetic=PRIVATE_COOKIE' in
                actual[READ_12].headers.get('cookie', '')) is readbook_has_cookie
        assert ('synthetic=PRIVATE_COOKIE' in
                actual[landing].headers.get('cookie', '')) is confirmation_has_cookie
        events = [
            kwargs['detail'] for args, kwargs in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
            and kwargs['detail'].get('phase') == 'delivery_http'
        ]
        assert [event['path_kind'] for event in events][:2] == [
            'readbook', 'confirmation_page',
        ]
        assert events[0]['cookie_header_present'] is readbook_has_cookie
        assert events[1]['cookie_header_present'] is confirmation_has_cookie
        assert 'PRIVATE_COOKIE' not in repr(diagnostics.events)
        assert 'synthetic=' not in repr(diagnostics.events)
        assert 'ref=' not in repr(diagnostics.events)
        assert 'disp=' not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('form_ref,allowed', (
    ('FICTIONAL', True),
    ('UNRELATED_PRIVATE_ORDER', False),
    ('FICTIONAL&amp;ref=UNRELATED_PRIVATE_ORDER', False),
    ('', False),
))
def test_confirmation_form_order_reference_must_match_navigated_order(
    form_ref, allowed,
):
    """A JS modal bound to readBook may not validate a different form order.

    The check compares opaque refs in memory only; neither ref is emitted to
    the caller, diagnostics, exception text, nor acquisition broker.
    """
    html = FIXTURE.read_text().replace(
        'ref=FICTIONAL&amp;disp=1',
        'ref=' + form_ref + '&amp;disp=1', 1,
    )
    if allowed:
        assert delivery_url(
            html, CONFIRMATION, allowed_origins=ALLOWED,
            expected_readbook=READ_12, require_bound_confirmation=True,
        ) == SIGNED
    else:
        with pytest.raises(DeliverySelectionError, match='ambiguous') as exc:
            delivery_url(
                html, CONFIRMATION, allowed_origins=ALLOWED,
                expected_readbook=READ_12, require_bound_confirmation=True,
            )
        assert 'FICTIONAL' not in str(exc.value)
        assert 'UNRELATED_PRIVATE_ORDER' not in str(exc.value)


@pytest.mark.parametrize('login_widget', (False, True))
def test_mismatched_confirmation_form_reference_blocks_acsm_http(
    tmp_path, login_widget,
):
    """No ACSM GET even when the card/button/Links identify this readBook."""
    landing = CONFIRMATION.replace('disp=1', 'disp=simple')
    html = FIXTURE.read_text().replace(
        'ref=FICTIONAL&amp;disp=1',
        'ref=UNRELATED_PRIVATE_ORDER&amp;disp=simple', 1,
    )
    if login_widget:
        html = html.replace(
            '<form method="post"', '<input type="password" value="PRIVATE_HTML" />'
            '<form method="post"', 1,
        )
    visited = []

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY,
                                  headers={'content-type': 'text/html'})
        if url == READ_12:
            return httpx.Response(302, headers={'location': landing})
        assert url == landing, 'A mismatched order must never reach the ACSM'
        return httpx.Response(200, text=html, headers={'content-type': 'text/html'})

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        book = session.library()[0]
        with pytest.raises(ProviderError):
            session.download_direct(book, tmp_path)
        assert visited == [LIBRARY, LIBRARY, READ_12, landing]
        assert session.connected is True
        assert not importer.calls
        events = [
            kw['detail'] for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        ]
        selections = [event for event in events if event.get('phase') == 'html_selection']
        assert len(selections) == 1
        assert selections[0]['code'] == 'html_delivery_ambiguous'
        assert events[-1]['code'] == (
            'login_form_detected' if login_widget else 'html_delivery_ambiguous'
        )
        assert 'UNRELATED_PRIVATE_ORDER' not in repr(diagnostics.events)
        assert 'FICTIONAL' not in repr(diagnostics.events)
        assert 'PRIVATE_HTML' not in repr(diagnostics.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_form_pointing_to_confirmation_on_other_html_is_not_a_ref_mismatch():
    """A landing URL has no ref to compare: preserve the historical route.

    This test is only about lack of contradictory GET evidence, not permission
    to follow a login-shaped page or a new issuer endpoint.
    """
    landing = 'https://www.numilog.com/Pages/Compte/Landing.aspx'
    assert delivery_url(
        FIXTURE.read_text(), landing, allowed_origins=ALLOWED,
        expected_readbook=READ_12,
    ) == SIGNED


@pytest.mark.parametrize('bad_query', (
    'ref=FICTIONAL&ref=SECOND_PRIVATE&disp=simple',
    'ref=FICTIONAL&disp=simple&unparsed',
    'ref=FICTIONAL&disp=simple&' + '&'.join(f'x{i}=v' for i in range(40)),
))
def test_confirmation_get_query_ambiguity_rejects_even_matching_html_form(bad_query):
    """Bounds/strict parsing apply to the actual GET, not only form action."""
    url = 'https://www.numilog.com/Pages/Commande/ConfirmationPaiement.aspx?' + bad_query
    with pytest.raises(DeliverySelectionError, match='ambiguous') as exc:
        delivery_url(
            FIXTURE.read_text(), url, allowed_origins=ALLOWED,
            expected_readbook=READ_12, require_bound_confirmation=True,
        )
    assert 'SECOND_PRIVATE' not in str(exc.value)
    assert 'FICTIONAL' not in str(exc.value)
