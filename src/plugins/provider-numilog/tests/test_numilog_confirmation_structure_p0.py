"""#1883: actual page relationships, wholly fictional data and MockTransport.

The private attachment was inspected offline, never copied into this fixture.
Only structure is retained: global LCP/ADOBE alternatives for one product/copy,
a ModalDownload button, a distinct readBook lid and ordinary site navigation.
This tests HTTP-client/importer boundaries, not a live merchant or real DRM.
"""
from pathlib import Path

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
    'missing_modal', 'duplicate_signed', 'unqualified_landing',
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
