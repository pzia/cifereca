"""P0 Numilog: a purchased ADEPT title need not expose online streaming.

All HTML, keys and signed URLs are fictional. Transport is MockTransport only.
The extra identity proof is a direct checked 302 from the selected *owned*
readBook?m=dwld to a single-book confirmation, never an isolated ACSM link.
"""
from pathlib import Path

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_html import DeliverySelectionError, delivery_url
from tests.test_numilog_confirmation_structure_p0 import (
    ALLOWED, CONFIRMATION, FIXTURE, INVENTORY, LIBRARY, READ_12,
)
from tests.test_numilog_signed_acsm_transport_p0 import (
    ACSM, SIGNED, _assert_clean, setup,
)


READ_DWLD = READ_12.replace('m=download', 'm=dwld')
INVENTORY_DWLD = INVENTORY.replace('m=download', 'm=dwld')
LANDING = CONFIRMATION.replace('disp=1', 'disp=simple')


def confirmation_without_stream(*, login_widget=False):
    source = FIXTURE.read_text()
    streaming = (
        '<a class="btn btn-block btn-option buttonlog" '
        'href="/Pages/Compte/readBook.ashx?lid=12&amp;m=download" '
        'target="_blank">Autre accès</a>'
    )
    assert source.count(streaming) == 1
    html = source.replace(streaming, '').replace('disp=1', 'disp=simple')
    if login_widget:
        html = html.replace(
            '<form method="post"',
            '<input type="password" value="FAKE_SITE_WIDGET" />'
            '<form method="post"', 1,
        )
    return html


@pytest.mark.parametrize('login_widget', (False, True))
def test_owned_readbook_302_without_streaming_imports_only_selected_adobe(
    tmp_path, login_widget,
):
    html = confirmation_without_stream(login_widget=login_widget)
    requests = []

    def handler(request):
        url = str(request.url)
        requests.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY_DWLD,
                                  headers={'content-type': 'text/html'})
        if url == READ_DWLD:
            return httpx.Response(302, headers={'location': LANDING})
        if url == LANDING:
            return httpx.Response(200, text=html,
                                  headers={'content-type': 'text/html'})
        if url == SIGNED:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('Only the owned readBook, confirmation, and one ACSM are allowed')

    session, diagnostics, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        book = session.library()[0]
        result, origin = session.download_direct(book, tmp_path)
        assert result.is_file() and origin == LIBRARY
        assert requests == [LIBRARY, LIBRARY, READ_DWLD, LANDING, SIGNED]
        assert len(importer.calls) == 1
        assert importer.calls[0][1]['identity_id'] == 'fictional-reader'
        events = [
            kw['detail'] for args, kw in diagnostics.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        ]
        selection = [event for event in events if event.get('phase') == 'html_selection']
        assert [event['code'] for event in selection] == ['single_allowed_candidate']
        assert events[-1]['code'] == 'acsm_complete'
        assert not any('m=stream' in url for url in requests)
        assert all('FICTIONAL' not in str(e) and 'SYNTHETIC_NOT_A_CREDENTIAL' not in str(e)
                   for e in events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('mutation', (
    'another-card', 'another-modal', 'foreign-readbook', 'foreign-lcp',
    'contradictory-ref', 'another-adobe',
))
def test_missing_stream_never_bypasses_single_purchase_binding(mutation):
    html = confirmation_without_stream()
    if mutation == 'another-card':
        html = html.replace(
            '    <a href="https://www.numilog.com/Pages/Compte/MesLivres.aspx">Bibliothèque</a>',
            '    <div class="card">Autre livre</div>'
            '    <a href="https://www.numilog.com/Pages/Compte/MesLivres.aspx">Bibliothèque</a>',
        )
    elif mutation == 'another-modal':
        html = html.replace('</form>', '<a onclick="ModalDownload(9002, 1)">Other</a></form>')
    elif mutation == 'foreign-readbook':
        html = html.replace(
            '</form>',
            '<a href="/Pages/Compte/readBook.ashx?lid=34&amp;m=stream">Other</a></form>',
        )
    elif mutation == 'foreign-lcp':
        html = html.replace(
            "ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'LCP'",
            "ProductId: 9002, IndexExemplaire: 1, DrmTechnlology: 'LCP'",
        )
    elif mutation == 'contradictory-ref':
        html = html.replace('ref=FICTIONAL&amp;disp=simple',
                            'ref=UNRELATED&amp;disp=simple', 1)
    else:
        html = html.replace(
            '</script>',
            "{ ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'ADOBE',"
            " link: 'https://entrepot2r.numilog.com/fulfillment/URLLink.acsm?"
            "action=enterorder&auth=SECOND_FAKE' },"
            "</script>", 1,
        )
    with pytest.raises(DeliverySelectionError) as raised:
        delivery_url(
            html, LANDING, allowed_origins=ALLOWED,
            expected_readbook=READ_DWLD,
            require_bound_confirmation=True,
            allow_missing_readbook=True,
        )
    assert raised.value.code in {'ambiguous', 'missing'}
    assert 'FICTIONAL' not in str(raised.value)


def test_no_stream_parser_requires_explicit_redirect_attestation():
    html = confirmation_without_stream(login_widget=True)
    with pytest.raises(DeliverySelectionError):
        delivery_url(
            html, LANDING, allowed_origins=ALLOWED,
            expected_readbook=READ_DWLD, require_bound_confirmation=True,
        )


def test_no_stream_requires_exact_dwld_mode_and_simple_ref():
    html = confirmation_without_stream()
    for expected, landing in (
        (READ_12, LANDING),  # m=download is not the attested dwld
        (READ_DWLD, CONFIRMATION),  # disp=1 cannot relax absent identity evidence
        (READ_DWLD, LANDING + '&ref=ANOTHER'),
        (READ_DWLD, 'https://www.numilog.com/Pages/Compte/Other.aspx?ref=FICTIONAL&disp=simple'),
    ):
        with pytest.raises(DeliverySelectionError):
            delivery_url(
                html, landing, allowed_origins=ALLOWED,
                expected_readbook=expected, require_bound_confirmation=True,
                allow_missing_readbook=True,
            )


def test_no_stream_two_hop_redirect_does_not_reuse_stale_source_proof(tmp_path):
    html = confirmation_without_stream()
    waypoint = 'https://www.numilog.com/Pages/Compte/Relay.aspx'
    requests = []

    def handler(request):
        url = str(request.url)
        requests.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY_DWLD)
        if url == READ_DWLD:
            return httpx.Response(302, headers={'location': waypoint})
        if url == waypoint:
            return httpx.Response(302, headers={'location': LANDING})
        if url == LANDING:
            return httpx.Response(200, text=html)
        pytest.fail('No ACSM should be requested without direct attestation')

    session, _, importer = setup(tmp_path, handler)
    session.connected = True
    try:
        book = session.library()[0]
        with pytest.raises(ProviderError):
            session.download_direct(book, tmp_path)
        assert requests == [LIBRARY, LIBRARY, READ_DWLD, waypoint, LANDING]
        assert importer.calls == []
        _assert_clean(tmp_path)
    finally:
        session.close()
