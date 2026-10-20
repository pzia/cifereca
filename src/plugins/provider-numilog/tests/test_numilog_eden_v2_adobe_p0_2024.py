"""P0 #2024: Eden V2 ADOBE+LCP are format alternatives of one owned copy.

Only invented tokens and httpx.MockTransport: zero real vendor requests.
The LCP alternative must never be fetched or interpreted as Adobe.
"""
from __future__ import annotations

import re
import socket

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_html import (
    DeliverySelectionError, _attested_public_https, delivery_url,
)
from tests.test_numilog_no_stream_confirmation_p0 import (
    confirmation_without_stream, INVENTORY_DWLD, READ_DWLD, LANDING,
)
from tests.test_numilog_confirmation_structure_p0 import LIBRARY
from tests.test_numilog_signed_acsm_transport_p0 import (
    ACSM, _assert_clean, setup,
)

BASE = (
    'https://services.numilog.com/v4.0/HandlerDownloadLinks/'
    'HandlerEdenDownloadLinkV2.ashx'
)
SIGNED_ADOBE = BASE + '?pid=9001&uid=1111111111111111&tid=2222222222222222'
SIGNED_LCP = SIGNED_ADOBE + '&DRM=LCP'
ALLOWED = {
    ('https', 'www.numilog.com', 443),
    ('https', 'entrepot2r.numilog.com', 443),
    ('https', 'services.numilog.com', 443),
}


def eden_confirmation(*, login_widget=True, stream=False, mode='dwld'):
    page = confirmation_without_stream(login_widget=login_widget)
    if stream:
        page = page.replace(
            '<a class="btn btn-block btn-checkout3"',
            '<a href="/Pages/Compte/readBook.ashx?lid=12&amp;m=stream"'
            ' class="btn btn-option buttonlog">Lire en ligne</a>'
            '<a class="btn btn-block btn-checkout3"', 1,
        )
    old_lcp = (
        r"\{ ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'LCP',"
        r" link: '[^']+' \},"
    )
    old_adobe = (
        r"\{ ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'ADOBE',"
        r" link: '[^']+' \},"
    )
    lcp = (
        "{ ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'LCP', "
        "link: '" + SIGNED_LCP + "', format: 'EPUB' },"
    )
    adobe = (
        "{ ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'ADOBE', "
        "link: '" + SIGNED_ADOBE + "' },"
    )
    page, n1 = re.subn(old_lcp, lambda _: lcp, page)
    page, n2 = re.subn(old_adobe, lambda _: adobe, page)
    assert n1 == n2 == 1
    if mode == 'download':
        page = page.replace('m=dwld', 'm=download')
    return page


@pytest.mark.parametrize('with_login', [False, True])
@pytest.mark.parametrize('mode', ['dwld', 'download'])
def test_owned_302_selects_only_adobe_eden_v2_and_imports_acsm(
    tmp_path, with_login, mode,
):
    requested = []
    book_url = READ_DWLD if mode == 'dwld' else READ_DWLD.replace('m=dwld', 'm=download')
    inventory = INVENTORY_DWLD if mode == 'dwld' else INVENTORY_DWLD.replace('m=dwld', 'm=download')
    page = eden_confirmation(login_widget=with_login)

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=inventory, headers={'content-type': 'text/html'})
        if url == book_url:
            return httpx.Response(302, headers={'location': LANDING})
        if url == LANDING:
            return httpx.Response(200, text=page, headers={'content-type': 'text/html'})
        if url == SIGNED_ADOBE:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('No LCP, second purchase, public search, or unbound GET allowed')

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        owned = session.library()[0]
        file, origin = session.download_direct(owned, tmp_path)
        assert file.is_file() and origin == LIBRARY
        assert requested == [LIBRARY, LIBRARY, book_url, LANDING, SIGNED_ADOBE]
        assert SIGNED_LCP not in requested
        assert len(broker.calls) == 1
        terminal = [kw['detail'] for args, kw in diag.events
                    if args[:2] == ('NUMILOG', 'acquisition_delivery')
                    and kw['detail']['phase'] == 'completed']
        assert len(terminal) == 1 and terminal[0]['code'] == 'acsm_complete'
        assert '1111111111111111' not in repr(diag.events)
        assert '2222222222222222' not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('with_login', [True, False])
def test_page_with_stream_link_still_matches_one_owned_adobe(tmp_path, with_login):
    html = eden_confirmation(login_widget=with_login, stream=True)
    assert delivery_url(
        html, LANDING, expected_readbook=READ_DWLD,
        allowed_origins=ALLOWED, require_bound_confirmation=True,
    ) == SIGNED_ADOBE


def test_unbound_eden_and_lcp_are_not_automatically_downloaded(tmp_path):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        pytest.fail('Neither unbound Eden ADOBE nor LCP may be requested')

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        for url in (SIGNED_ADOBE, SIGNED_LCP):
            with pytest.raises(ProviderError, match='non liée'):
                session._download(url, tmp_path / 'book.epub', source='direct_url')
        assert requested == [] and broker.calls == []
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('attack', [
    'adobe_lcp_flag', 'wrong_product', 'wrong_copy', 'wrong_pid',
    'lcp_other_product', 'private_ip', 'local_name', 'http_link',
    'duplicate_adobe', 'duplicate_lcp', 'lcp_only', 'adobe_only_other_host',
    'duplicate_query', 'userinfo', 'fragment',
    'unrelated_modal', 'wrong_order_ref', 'foreign_card', 'other_readbook',
])
def test_malformed_or_foreign_eden_alternative_never_authorizes_get(attack):
    html = eden_confirmation()
    if attack == 'adobe_lcp_flag':
        html = html.replace(SIGNED_ADOBE + "'", SIGNED_LCP + "'")
    elif attack == 'wrong_product':
        html = html.replace(
            "ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'ADOBE'",
            "ProductId: 9002, IndexExemplaire: 1, DrmTechnlology: 'ADOBE'",
        )
    elif attack == 'wrong_copy':
        html = html.replace(
            "IndexExemplaire: 1, DrmTechnlology: 'ADOBE'",
            "IndexExemplaire: 2, DrmTechnlology: 'ADOBE'",
        )
    elif attack == 'wrong_pid':
        html = html.replace('pid=9001', 'pid=9002')
    elif attack == 'lcp_other_product':
        html = html.replace(
            "ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'LCP'",
            "ProductId: 9002, IndexExemplaire: 1, DrmTechnlology: 'LCP'",
        )
    elif attack == 'duplicate_adobe':
        html = html.replace("];", "{ ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'ADOBE', link: '" + SIGNED_ADOBE + "' },];", 1)
    elif attack == 'duplicate_lcp':
        html = html.replace("];", "{ ProductId: 9001, IndexExemplaire: 1, DrmTechnlology: 'LCP', link: '" + SIGNED_LCP + "' },];", 1)
    elif attack == 'lcp_only':
        html = html.replace("DrmTechnlology: 'ADOBE'", "DrmTechnlology: 'LCP'")
    elif attack == 'adobe_only_other_host':
        html = html.replace(SIGNED_ADOBE, SIGNED_ADOBE.replace('services.numilog.com', 'evil.invalid'))
    elif attack == 'private_ip':
        html = html.replace(SIGNED_ADOBE, SIGNED_ADOBE.replace('services.numilog.com', '127.0.0.1'))
    elif attack == 'local_name':
        html = html.replace(SIGNED_ADOBE, SIGNED_ADOBE.replace('services.numilog.com', 'localhost'))
    elif attack == 'http_link':
        html = html.replace(SIGNED_ADOBE, SIGNED_ADOBE.replace('https://', 'http://'))
    elif attack == 'userinfo':
        html = html.replace(SIGNED_ADOBE, SIGNED_ADOBE.replace('https://', 'https://fake:fake@'))
    elif attack == 'fragment':
        html = html.replace(SIGNED_ADOBE, SIGNED_ADOBE + '#bad')
    elif attack == 'duplicate_query':
        html = html.replace(SIGNED_ADOBE, SIGNED_ADOBE + '&pid=9001')
    elif attack == 'unrelated_modal':
        html = html.replace('ModalDownload(9001, 1)', 'ModalDownload(9002, 1)')
    elif attack == 'wrong_order_ref':
        html = html.replace('ref=FICTIONAL&amp;disp=simple', 'ref=UNRELATED&amp;disp=simple', 1)
    elif attack == 'foreign_card':
        html = html.replace('class="card mp-4"', 'class="card mp-4"><div class="card">Extra</div>', 1)
    else:
        html = html.replace('</form>', '<a href="/Pages/Compte/readBook.ashx?lid=34&amp;m=stream">Extra</a></form>', 1)
    assert html != eden_confirmation()
    with pytest.raises(DeliverySelectionError):
        delivery_url(html, LANDING, allowed_origins=ALLOWED,
                     expected_readbook=READ_DWLD,
                     require_bound_confirmation=True, allow_missing_readbook=True)


def test_public_https_policy_is_independent_from_known_endpoints():
    assert _attested_public_https(SIGNED_ADOBE)
    assert _attested_public_https(SIGNED_LCP)  # safe URL, NOT LCP support
    assert _attested_public_https('https://publisher-delivery.org/new/route?license=FAKE')
    for disallowed in (
        'http://publisher-delivery.org/file',
        'https://localhost/delivery',
        'https://127.0.0.1/delivery',
        'https://[::1]/delivery',
        'https://metadata.google.internal/license',
        'https://username:password@publisher-delivery.org/file',
        'https://publisher-delivery.org:8443/file',
        'https://publisher-delivery.org/file#fragment',
    ):
        assert not _attested_public_https(disallowed)


@pytest.mark.parametrize('outcome', ['html', 'bad_acsm', 'forbidden', 'redirect_offsite'])
def test_failed_vendor_response_never_publishes_or_switches_to_lcp(tmp_path, outcome):
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY_DWLD)
        if url == READ_DWLD:
            return httpx.Response(302, headers={'location': LANDING})
        if url == LANDING:
            return httpx.Response(200, text=eden_confirmation(),
                                  headers={'content-type': 'text/html'})
        assert url == SIGNED_ADOBE
        if outcome == 'forbidden':
            return httpx.Response(403)
        if outcome == 'redirect_offsite':
            return httpx.Response(302, headers={'location': 'https://evil.invalid/'})
        if outcome == 'html':
            return httpx.Response(200, text='<input type="password"/>',
                                  headers={'content-type': 'text/html'})
        return httpx.Response(200, content=b'NO_ACSM',
                              headers={'content-type': 'application/vnd.adobe.adept+xml'})

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        with pytest.raises(ProviderError):
            session.download_direct(session.library()[0], tmp_path)
        assert seen == [LIBRARY, LIBRARY, READ_DWLD, LANDING, SIGNED_ADOBE]
        assert not broker.calls
        assert not list(tmp_path.glob('*.epub'))
        _assert_clean(tmp_path)
    finally:
        session.close()


PUBLIC_VENDOR = 'https://future-download.numilog.com/path/future?token=FAKE_DELIVERY'
PUBLIC_CDN = 'https://publisher-cdn.org/opaque/file?receipt=FICTIONAL'
PUBLIC_LCP = 'https://different-publisher.org/read-lcp?token=FAKE_LCP'


def new_vendor_confirmation():
    # Both entries belong to the same ProductId and exemplaire even though
    # the delivery hosts and opaque queries are different.
    return (eden_confirmation()
            .replace(SIGNED_LCP, PUBLIC_LCP)
            .replace(SIGNED_ADOBE, PUBLIC_VENDOR))


def _fake_dns(request_host, port, **kwargs):
    assert request_host in {'future-download.numilog.com', 'publisher-cdn.org'}
    assert port == 443
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, '',
             ('8.8.8.8', 443))]


@pytest.mark.parametrize('delivery_redirect', [False, True])
def test_future_public_host_from_verified_links_no_static_endpoint(
    tmp_path, monkeypatch, delivery_redirect,
):
    # This includes a subdomain receiving the Numilog wildcard Cookie in an
    # ordinary httpx call. The selected signed link MUST not receive it.
    import cifereca_numilog as plugin
    monkeypatch.setattr(plugin.socket, 'getaddrinfo', _fake_dns)
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append((url, request.headers.get('cookie')))
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY_DWLD)
        if url == READ_DWLD:
            return httpx.Response(302, headers={'location': LANDING})
        if url == LANDING:
            return httpx.Response(200, text=new_vendor_confirmation(),
                                  headers={'content-type': 'text/html'})
        if url == PUBLIC_VENDOR and delivery_redirect:
            return httpx.Response(302, headers={'location': PUBLIC_CDN})
        if url in {PUBLIC_VENDOR, PUBLIC_CDN}:
            return httpx.Response(
                200, content=ACSM,
                headers={'content-type': 'application/vnd.adobe.adept+xml'},
            )
        pytest.fail('No LCP GET, arbitrary URL, or other purchase allowed')

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    session.client.cookies.set('session', 'FAKE_COOKIE_SECRET',
                               domain='.numilog.com')
    try:
        item = session.library()[0]
        file, origin = session.download_direct(item, tmp_path)
        assert file.is_file() and origin == LIBRARY
        urls = [url for url, cookie in seen]
        assert urls == ([LIBRARY, LIBRARY, READ_DWLD, LANDING, PUBLIC_VENDOR]
                        + ([PUBLIC_CDN] if delivery_redirect else []))
        assert PUBLIC_LCP not in urls
        assert all(cookie in (None, '') for url, cookie in seen
                   if url in {PUBLIC_VENDOR, PUBLIC_CDN})
        assert len(broker.calls) == 1
        assert 'FAKE_COOKIE_SECRET' not in repr(diag.events)
        assert 'FAKE_DELIVERY' not in repr(diag.events)
        _assert_clean(tmp_path)
    finally:
        session.close()


@pytest.mark.parametrize('unsafe_resolution', [
    '10.0.0.1', '127.0.0.1', '169.254.169.254', '192.168.1.3',
])
def test_attested_public_dns_must_not_resolve_to_private_ip(
    tmp_path, monkeypatch, unsafe_resolution,
):
    import cifereca_numilog as plugin

    def private_dns(host, port, **kwargs):
        assert host == 'future-download.numilog.com' and port == 443
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, '',
                 (unsafe_resolution, 443))]

    monkeypatch.setattr(plugin.socket, 'getaddrinfo', private_dns)
    visited = []

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=INVENTORY_DWLD)
        if url == READ_DWLD:
            return httpx.Response(302, headers={'location': LANDING})
        if url == LANDING:
            return httpx.Response(200, text=new_vendor_confirmation(),
                                  headers={'content-type': 'text/html'})
        pytest.fail('Rejected host must not be queried')

    session, diag, broker = setup(tmp_path, handler)
    session.connected = True
    try:
        with pytest.raises(ProviderError, match='non publique'):
            session.download_direct(session.library()[0], tmp_path)
        assert visited == [LIBRARY, LIBRARY, READ_DWLD, LANDING]
        assert broker.calls == []
        assert not list(tmp_path.glob('*.epub'))
        _assert_clean(tmp_path)
    finally:
        session.close()


def test_arbitrary_new_host_requires_real_selected_links_and_owned_modal():
    html = new_vendor_confirmation()
    selected = {}
    assert delivery_url(
        html, LANDING, allowed_origins=ALLOWED,
        expected_readbook=READ_DWLD, require_bound_confirmation=True,
        allow_missing_readbook=True, selection=selected,
    ) == PUBLIC_VENDOR
    assert selected == {'kind': 'acsm'}
    # A bare vendor URL (or the LCP alternative) in other HTML is not owned.
    for evil in (PUBLIC_VENDOR, PUBLIC_LCP):
        with pytest.raises(DeliverySelectionError):
            delivery_url(
                '<script>const possible = "' + evil + '";</script>',
                LANDING, allowed_origins=ALLOWED,
                expected_readbook=READ_DWLD, require_bound_confirmation=True,
                allow_missing_readbook=True,
            )
