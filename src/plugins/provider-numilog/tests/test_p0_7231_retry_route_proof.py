"""#7231: only the whole exact offer, not the last EPUB hop, qualifies replay."""
import httpx
import pytest

import cifereca_numilog as provider
from cifereca.plugin_api import ProviderError, RetryableContentDownload
from tests.pinned_public_https_fixture import mock_selected_public_client_from_session
from tests.test_p1_3252_selected_delivery_candidate import setup, selected
from tests.test_p1_3637_selected_candidate_staging import _direct_epub_session
from tests.test_p0_2797_selected_readbook_dwld_offer import card, page, rig, chosen_offer
from tests.test_numilog_no_stream_confirmation_p0 import LANDING
from tests.test_numilog_not_specified_epub_confirmation_p0_2024 import SIGNED_EPUB, confirmation
from tests.test_numilog_owned_js_delivery_p0 import SIGNED


READBOOK = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=12&m=dwld"


@pytest.mark.parametrize("route", ["direct_epub", "direct_acsm", "readbook", "confirmation", "confirmation_epub"])
@pytest.mark.parametrize("failure", ["503", "timeout"])
def test_selected_transport_qualifies_only_initial_direct_epub(tmp_path, monkeypatch, route, failure):
    if route == "direct_epub":
        session, account, reads, offer = _direct_epub_session()
    elif route == "direct_acsm":
        session, account, _, reads, offer = setup()
    else:
        session, _, reads = rig(page(card()))
        account, _, offer = chosen_offer(session)
    calls = []
    def handler(request):
        url = str(request.url)
        calls.append(url)
        if route in ("confirmation", "confirmation_epub") and url == READBOOK:
            return httpx.Response(302, headers={"location": LANDING})
        if route == "confirmation_epub" and url == LANDING:
            return httpx.Response(200, text=confirmation(),
                                  headers={"content-type": "text/html"})
        if failure == "timeout":
            raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_URL", request=request)
        return httpx.Response(503, content=b"SYNTHETIC_PRIVATE_BODY")
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler),
                                  trust_env=False, follow_redirects=False)
    mock_selected_public_client_from_session(monkeypatch, provider, session)
    try:
        with pytest.raises(ProviderError) as error:
            session.rights_selected_delivery_candidate(account, **selected(offer, tmp_path))
        assert (type(error.value) is RetryableContentDownload) == (route == "direct_epub")
        assert "SYNTHETIC_PRIVATE" not in str(error.value)
        assert list(tmp_path.iterdir()) == []
        expected = {
            "direct_epub": [SIGNED_EPUB], "direct_acsm": [SIGNED],
            "readbook": [READBOOK], "confirmation": [READBOOK, LANDING],
            "confirmation_epub": [READBOOK, LANDING, SIGNED_EPUB],
        }
        assert calls == expected[route]
    finally:
        session.close()


@pytest.mark.parametrize("change", ["caller", "consent", "owner"])
def test_direct_epub_failure_after_scope_change_is_not_retryable(tmp_path, monkeypatch, change):
    session, account, reads, offer = _direct_epub_session()
    live = [True]
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if change == "caller":
            account.unique_id = "foreign"
        elif change == "consent":
            live[0] = False
        else:
            session._accounts[0].identity_id = "foreign"
        return httpx.Response(503)
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler),
                                  trust_env=False, follow_redirects=False)
    mock_selected_public_client_from_session(monkeypatch, provider, session)
    try:
        with pytest.raises(ProviderError) as error:
            session.rights_selected_delivery_candidate(
                account, **selected(offer, tmp_path, admission_current=lambda: live[0]),
            )
        assert type(error.value) is not RetryableContentDownload
        assert calls == [SIGNED_EPUB]
        assert not list(tmp_path.iterdir())
    finally:
        session.close()
