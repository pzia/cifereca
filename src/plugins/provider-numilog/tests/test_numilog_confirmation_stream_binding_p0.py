"""#2024 P0: owned readBook m=dwld and confirmation streaming m=stream.

Numilog's confirmation Streaming anchor is only edition identity evidence.
A signed Adobe ACSM from the unique Links/ModalDownload pair is the *only*
fulfillment URL. These are synthetic records with no account credentials.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_html import (
    DeliverySelectionError, _confirmation_readbook_binding,
    _readbook_coordinates, delivery_url,
)
from tests.test_numilog_confirmation_structure_p0 import (
    ACSM, ALLOWED, CONFIRMATION, FIXTURE, INVENTORY, LIBRARY,
    READ_12, SIGNED,
)
from tests.test_numilog_direct_confirmation_p0 import (
    DIRECT, inventory_with_first_card_link, confirmation_with_matching_synthetic_order,
)
from tests.test_numilog_signed_acsm_transport_p0 import setup, _assert_clean

READ_DWLD = READ_12.replace('m=download', 'm=dwld')
READ_STREAM = READ_12.replace('m=download', 'm=stream')
INVENTORY_DWLD = INVENTORY.replace('lid=12&amp;m=download', 'lid=12&amp;m=dwld')
CONFIRM_SIMPLE = CONFIRMATION.replace('disp=1', 'disp=simple')


def _html_stream(*, direct=False, widget=False):
    html = confirmation_with_matching_synthetic_order() if direct else FIXTURE.read_text()
    html = html.replace('m=download', 'm=stream')
    html = html.replace(
        'class="btn btn-block btn-option buttonlog"',
        'id="ctl02_CentrePage_RepeaterConfirmationPaiement_ctl00_HyperLinkStreamingConfirmation" '
        'class="btn btn-option buttonlog btn-block"',
    ).replace('Autre accès', 'Lire en ligne (Streaming)')
    if not direct:
        html = html.replace('disp=1', 'disp=simple')
    if widget:
        html = html.replace(
            '<form method="post"',
            '<input type="password" name="template-login" value="SECRET_HTML" />'
            '<form method="post"', 1,
        )
    return html


def _attested_pair(expected, observed):
    return _confirmation_readbook_binding(
        _readbook_coordinates(observed, CONFIRM_SIMPLE),
        _readbook_coordinates(expected, CONFIRM_SIMPLE),
    )


@pytest.mark.parametrize('expected,observed,allow', (
    ('lid=12&m=dwld', 'lid=12&m=stream', True),
    ('m=dwld&lid=12', 'lid=12&m=stream', True),
    ('lid=12&m=dwld&edition=1', 'edition=1&m=stream&lid=12', True),
    ('lid=12&m=dwld&edition=A&edition=B',
     'edition=A&edition=B&lid=12&m=stream', True),
    ('lid=12&m=download', 'lid=12&m=download', True),  # unchanged exact rule
    ('lid=12&m=dwld', 'lid=12&m=dwld', True),          # unchanged exact rule
    ('lid=12&m=dwld', 'lid=34&m=stream', False),
    ('lid=12&m=download', 'lid=12&m=stream', False),
    ('lid=12&m=stream', 'lid=12&m=dwld', False),
    ('lid=12&m=dwld', 'lid=12&m=other', False),
    ('lid=12&m=other', 'lid=12&m=stream', False),
    ('lid=12', 'lid=12&m=stream', False),
    ('lid=12&m=dwld', 'lid=12', False),
    ('lid=12&m=dwld', 'lid=12&m=stream&m=stream', False),
    ('lid=12&m=dwld&m=dwld', 'lid=12&m=stream', False),
    ('lid=12&m=dwld', 'lid=12&m=stream&copy=2', False),
    ('lid=12&m=dwld&copy=1', 'lid=12&m=stream', False),
    ('lid=12&m=dwld&copy=1', 'lid=12&m=stream&copy=2', False),
    ('lid=12&m=dwld&edition=A&edition=B',
     'lid=12&m=stream&edition=B&edition=A', False),
    ('lid=12&m=dwld&edition=A', 'lid=12&m=stream&edition=A&edition=A', False),
    ('lid=12&m=dwld', 'lid=012&m=stream', False),
    ('lid=12&m=dwld', 'lid=x&m=stream', False),
))
def test_only_directional_dwld_to_stream_same_lid_other_params_exact(
    expected, observed, allow,
):
    start = '/Pages/Compte/readBook.ashx?' + expected
    in_card = '/Pages/Compte/readBook.ashx?' + observed
    assert _attested_pair(start, in_card) is allow


def test_confirmation_binding_does_not_accept_unknown_readbook_origins():
    assert not _attested_pair(READ_DWLD, 'https://evil.invalid/Pages/Compte/readBook.ashx?lid=12&m=stream')
    assert not _attested_pair(READ_DWLD, 'https://www.numilog.com/Login.Aspx?lid=12&m=stream')
    assert not _attested_pair(READ_DWLD, '/Pages/Compte/readBook.ashx?lid=12&m=stream#token')
    assert not _attested_pair(READ_DWLD, '/Pages/Compte/readBook.ashx?lid=12&m=stream\n')
    assert not _attested_pair(READ_DWLD, '/Pages/Compte/readBook.ashx?lid=12&m=stream&auth=PRIVATE')


@pytest.mark.parametrize('with_login_widget', (False, True))
@pytest.mark.parametrize('entry', ('readbook_302', 'owned_card_href'))
def test_real_shape_dwld_to_stream_downloads_only_signed_adobe(
    tmp_path, with_login_widget, entry,
):
    direct = entry == 'owned_card_href'
    inventory = (
        inventory_with_first_card_link().replace(
            'lid=12&amp;m=download', 'lid=12&amp;m=dwld',
        ) if direct else INVENTORY_DWLD
    )
    destination = DIRECT if direct else CONFIRM_SIMPLE
    html = _html_stream(direct=direct, widget=with_login_widget)
    requests = []

    def handler(request):
        url = str(request.url)
        requests.append(request)
        if url == LIBRARY:
            return httpx.Response(200, text=inventory,
                                  headers={'content-type': 'text/html'})
        if url == READ_DWLD:
            assert not direct, 'direct card navigation must not issue readBook'
            return httpx.Response(302, headers={'location': destination})
        if url == destination:
            return httpx.Response(200, text=html,
                                  headers={'content-type': 'text/html'})
        if url == SIGNED:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('Only the signed Adobe ACSM is a fulfillment URL')

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        items = session.library()
        assert len(items) == 2
        assert all(item.download_url is None for item in items)
        path, library = session.download_direct(items[0], tmp_path)
        assert path.is_file() and library == LIBRARY
        expected = [LIBRARY, LIBRARY] + (
            [DIRECT, SIGNED] if direct else [READ_DWLD, CONFIRM_SIMPLE, SIGNED]
        )
        assert [str(request.url) for request in requests] == expected
        assert READ_STREAM not in expected
        assert len(broker.calls) == 1
        details = [
            kw['detail'] for args, kw in diag.events
            if args[:2] == ('NUMILOG', 'acquisition_delivery')
        ]
        source = 'owned_card' if direct else 'readbook'
        assert all(d['source'] == source for d in details)
        assert details[-1]['code'] == 'acsm_complete'
        assert [d['path_kind'] for d in details if 'path_kind' in d] == (
            ['confirmation_page', 'acsm_fulfillment'] if direct
            else ['readbook', 'confirmation_page', 'acsm_fulfillment']
        )
        exported = repr(diag.events)
        for token in (
            'SECRET_HTML', 'FICTIONAL_LCP', 'FICTIONAL', 'PRIVATE_ORDER',
            'SYNTHETIC_NOT_A_CREDENTIAL', 'orderid=', 'resid=', 'auth=',
            'lid=12', 'm=stream', 'ref=', 'disp=',
        ):
            assert token not in exported
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('variation', (
    'other_lid', 'other_mode', 'stream_extra_copy', 'dwld_extra_copy',
    'double_stream_mode', 'double_stream_anchor', 'stream_outside_card',
    'wrong_product', 'wrong_copy', 'different_form_ref',
))
@pytest.mark.parametrize('with_login_widget', (False, True))
def test_reject_streaming_edition_mismatch_without_any_signed_get(
    tmp_path, variation, with_login_widget,
):
    inventory = INVENTORY_DWLD
    html = _html_stream(widget=with_login_widget)
    if variation == 'other_lid':
        html = html.replace('lid=12&amp;m=stream', 'lid=34&amp;m=stream')
    elif variation == 'other_mode':
        html = html.replace('lid=12&amp;m=stream', 'lid=12&amp;m=other')
    elif variation == 'stream_extra_copy':
        html = html.replace('m=stream', 'm=stream&amp;copy=2')
    elif variation == 'dwld_extra_copy':
        inventory = inventory.replace('m=dwld', 'm=dwld&amp;copy=2')
    elif variation == 'double_stream_mode':
        html = html.replace('m=stream', 'm=stream&amp;m=stream')
    elif variation == 'double_stream_anchor':
        stream = '<a href="/Pages/Compte/readBook.ashx?lid=12&amp;m=stream">Extra</a>'
        marker = '</div>\n    </div></div>'
        assert marker in html
        html = html.replace(marker, stream + marker, 1)
    elif variation == 'stream_outside_card':
        # Move the sole streaming identity link out of the owning .card.
        import re
        match = re.search(r'<a [^>]*HyperLinkStreamingConfirmation[^>]*>.*?</a>', html)
        assert match is not None
        original = match.group(0)
        html = html[:match.start()] + html[match.end():]
        marker = '</div></div>\n    <a href='
        assert marker in html
        html = html.replace(marker, '</div></div>' + original + '\n    <a href=', 1)
    elif variation == 'wrong_product':
        html = html.replace('ModalDownload(9001, 1)', 'ModalDownload(9002, 1)')
    elif variation == 'wrong_copy':
        html = html.replace('ModalDownload(9001, 1)', 'ModalDownload(9001, 2)')
    elif variation == 'different_form_ref':
        html = html.replace('ref=FICTIONAL&amp;disp=simple',
                            'ref=OTHER_ORDER&amp;disp=simple', 1)

    visited = []

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=inventory)
        if url == READ_DWLD or ('copy=2' in url and 'readBook.ashx' in url):
            return httpx.Response(302, headers={'location': CONFIRM_SIMPLE})
        assert url == CONFIRM_SIMPLE, 'Rejected candidate must never trigger a signed GET'
        return httpx.Response(200, text=html,
                              headers={'content-type': 'text/html'})

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        book = session.library()[0]
        with pytest.raises(ProviderError):
            session.download_direct(book, tmp_path)
        assert visited[:2] == [LIBRARY, LIBRARY]
        assert visited[-1] == CONFIRM_SIMPLE and len(visited) == 4
        assert SIGNED not in visited and READ_STREAM not in visited
        assert not broker.calls
        assert session.connected is True
        details = [kw['detail'] for args, kw in diag.events
                   if args[:2] == ('NUMILOG', 'acquisition_delivery')]
        assert details[-1]['code'] in {'html_delivery_ambiguous', 'login_form_detected'}
        assert all(token not in repr(diag.events) for token in (
            'SECRET_HTML', 'OTHER_ORDER', 'SYNTHETIC_NOT_A_CREDENTIAL',
            'lid=12', 'm=stream', 'orderid=', 'resid=', 'auth=',
        ))
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_streaming_link_never_licenses_a_download_without_modal_and_links():
    html = _html_stream().replace('ModalDownload(9001, 1)', 'UnrelatedAction(9001, 1)')
    with pytest.raises(DeliverySelectionError, match='ambiguous'):
        delivery_url(
            html, CONFIRM_SIMPLE, allowed_origins=ALLOWED,
            expected_readbook=READ_DWLD, require_bound_confirmation=True,
        )


@pytest.mark.parametrize('url', (
    'https://www.numilog.com/Pages/Compte/Landing.aspx',
    'https://www.numilog.com/Pages/Commande/Other.aspx?ref=FICTIONAL&disp=simple',
    'https://evil.invalid/Pages/Commande/ConfirmationPaiement.aspx?ref=FICTIONAL&disp=simple',
    'http://www.numilog.com/Pages/Commande/ConfirmationPaiement.aspx?ref=FICTIONAL&disp=simple',
))
def test_dwld_stream_equivalence_is_not_accepted_on_noncanonical_page(url):
    # A global Links array on a landing page does not confer the stricter
    # Numilog confirmation-only edition-binding exception.
    html = _html_stream()
    with pytest.raises(DeliverySelectionError, match='ambiguous'):
        delivery_url(
            html, url, allowed_origins=ALLOWED,
            expected_readbook=READ_DWLD, require_bound_confirmation=True,
        )
