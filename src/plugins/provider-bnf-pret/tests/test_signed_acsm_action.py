"""Synthetic structural fixtures use the control attributes attested in #2055."""
from html import escape
import json

import httpx
import pytest

from bnf_token_fixture import connected, TOKEN
from cifereca.plugin_api import ProviderError
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError
from cifereca_bnf_pret_signed_action import signed_acsm_action, signed_loan_url

QUERY = 'token=synthetic%2fTOKEN%2B+value&timestamp=opaque%3Atime&nonce=opaque%2bnonce&drm=acs4&device_type=reader'
URL = 'https://pret.bnf.fr/loans/loan-a?' + QUERY
CARD = ('<section class="active-loans"><div class="js-loan">'
        '<a href="/resources/book-a">Synthetic</a><a href="/loans/loan-a">Loan</a>'
        '</div></section>')


def action(url=URL, *, loan='loan-a'):
    return ('<a class="actions-list__link actions-list__sublink extra js-accordeon__item" '
            f'aria-controls="{loan}-reader-acs" href="{escape(url, quote=True)}">ACSM</a>')


def four_reader_actions():
    """Synthetic sibling accordion: four distinct signed routes for one loan."""
    web = URL.replace('drm=acs4&device_type=reader', 'viewer=web_reader')
    computer = URL.replace('device_type=reader', 'device_type=computer').replace('drm=acs4', 'drm=lcp')
    reader_lcp = URL.replace('drm=acs4', 'drm=lcp')
    return ('<ul class="actions-list js-accordeon">'
            '<li><a class="actions-list__title actions-list__link" target="_blank" '
            f'href="{escape(web, quote=True)}">Lire en ligne</a></li>'
            '<li><a class="actions-list__title actions-list__link js-accordeon__item" '
            f'href="{escape(computer, quote=True)}">Lire hors ligne sur ordinateur</a></li>'
            '<li><span>Lire sur une liseuse</span>'
            '<a class="actions-list__link actions-list__sublink js-accordeon__item" '
            f'href="{escape(reader_lcp, quote=True)}" aria-controls="loan-a-reader-lcp">'
            'Liseuse Vivlio/PocketBook ou Bookeen DIVA</a>'
            + action().replace('>ACSM</a>', '>Autre type de liseuse</a>')
            + '<div id="loan-a-reader-acs">Adobe Digital Editions : transférer sur liseuse</div>'
            '</li></ul>')


def test_four_distinct_sibling_actions_select_only_reader_acs4(tmp_path):
    html = CARD + four_reader_actions()
    assert signed_acsm_action(html, 'loan-a', 'book-a') == URL
    session, account, loan, state, _, downloaded = connected()
    state['activity'] = html
    try:
        file, public_url = session.download_digital(
            loan, account, tmp_path, explicit_user_action=True)
        assert file.read_bytes() == TOKEN and public_url == ''
        assert len(downloaded) == 1
        assert downloaded[0].url.raw_path == ('/loans/loan-a?' + QUERY).encode('ascii')
    finally:
        session.close()


def test_outside_card_control_is_joined_by_exact_aria_and_path():
    assert signed_acsm_action(CARD + action(), 'loan-a', 'book-a') == URL
    assert signed_acsm_action(CARD + action(loan='loan-b'), 'loan-a', 'book-a') is None
    assert signed_acsm_action(CARD + action(URL.replace('loan-a?', 'loan-b?')), 'loan-a', 'book-a') is None


@pytest.mark.parametrize('extra', [
    action(URL.replace('nonce=opaque%2bnonce', 'nonce=other')),
    action('https://other.invalid/loans/loan-a?' + QUERY),
    action('/loans/loan-a'),
])
def test_divergent_or_invalid_control_never_downgrades(extra):
    assert signed_acsm_action(CARD + action() + extra, 'loan-a', 'book-a') is None


def test_duplicate_identical_controls_are_one_action():
    assert signed_acsm_action(CARD + action() + action(), 'loan-a', 'book-a') == URL


def test_foreign_owning_card_cannot_override_exact_loan_selection():
    other_card = ('<div class="js-loan"><a href="/resources/foreign">Foreign</a>'
                  + action() + '</div>')
    assert signed_acsm_action(CARD + other_card, 'loan-a', 'book-a') is None


def test_unmarked_or_hidden_or_truncated_link_is_not_a_download_action():
    for anchor in [f'<a href="{escape(URL)}">Download</a>',
                   action().replace('<a ', '<a hidden '), action().replace('</a>', '')]:
        assert signed_acsm_action(CARD + anchor, 'loan-a', 'book-a') is None


@pytest.mark.parametrize('url', [
    URL + '&token=duplicate', URL + '&extra=unknown', URL.replace('nonce=', '%6eonce='),
    URL.replace('nonce=opaque%2bnonce', 'nonce='), URL.replace('nonce=opaque%2bnonce', 'nonce=%XX'),
    URL.replace('nonce=opaque%2bnonce', 'nonce=%0A'), URL + '#fragment', URL + '#',
    URL.replace('https://', '//'), URL.replace('https://', 'http://'),
    URL.replace('pret.bnf.fr', 'pret.bnf.fr:443'), URL.replace('pret.bnf.fr', 'u@pret.bnf.fr'),
    URL.replace('reader', 'browser'), URL.replace('acs4', 'lcp'),
    URL.replace('/loan-a?', '/loan-b?'),
])
def test_transport_validator_refuses_malformed_or_foreign_action_before_io(url):
    requests = []
    client = ReaderHTTPClient(transport=httpx.MockTransport(lambda r: requests.append(r)))
    try:
        assert signed_loan_url(url, 'loan-a') is None
        with pytest.raises(ReaderHTTPError, match='^url_refused$'):
            client.loan_token('loan-a', url)
        assert requests == []
    finally:
        client.close()


def test_single_authenticated_get_preserves_fresh_signature_bytes(tmp_path, caplog):
    session, account, loan, state, _, downloaded = connected()
    # The fresh URL differs from the one seen during inventory. It is outside
    # the card, so this exercises the explicit control relation, not ancestry.
    state['activity'] = CARD + action()
    try:
        with caplog.at_level('INFO', logger='cifereca'):
            file, public_url = session.download_digital(loan, account, tmp_path, explicit_user_action=True)
        assert len(downloaded) == 1 and downloaded[0].method == 'GET'
        assert downloaded[0].url.raw_path == ('/loans/loan-a?' + QUERY).encode('ascii')
        assert downloaded[0].headers.get('cookie') == 'reader=synthetic'
        assert file.read_bytes() == TOKEN and public_url == ''
        assert 'synthetic%2fTOKEN' not in caplog.text
        assert 'opaque%2bnonce' not in caplog.text
        assert 'token=' not in json.dumps(vars(loan))
    finally:
        session.close()


@pytest.mark.parametrize('html', [CARD, CARD + action(loan='loan-b'),
    CARD + action() + action(URL.replace('nonce=opaque%2bnonce', 'nonce=other'))])
def test_unavailable_action_never_gets_unsigned_url_or_stages(tmp_path, html):
    session, account, loan, state, _, downloaded = connected()
    state['activity'] = html
    try:
        with pytest.raises(ProviderError, match='^url_refused$'):
            session.download_digital(loan, account, tmp_path, explicit_user_action=True)
        assert downloaded == [] and not list(tmp_path.iterdir())
    finally:
        session.close()


def test_close_during_action_parse_revokes_get(tmp_path, monkeypatch):
    session, account, loan, _, _, downloaded = connected()
    namespace = session.download_digital.__func__.__globals__
    original = namespace['signed_acsm_action']
    def close_after_parse(*args):
        url = original(*args)
        session.close()
        return url
    monkeypatch.setitem(namespace, 'signed_acsm_action', close_after_parse)
    with pytest.raises(ProviderError, match='^session_closed$'):
        session.download_digital(loan, account, tmp_path, explicit_user_action=True)
    assert downloaded == [] and not list(tmp_path.iterdir())
