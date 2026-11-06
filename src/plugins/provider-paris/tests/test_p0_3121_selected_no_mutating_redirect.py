"""#3121 — selected Paris delivery never re-enters ASSARedirect.

Synthetic Paris provider and HTTP transport; no live lending request.
"""
from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest

from cifereca.plugin_api import ProviderConfigurationError
from tests.test_p0_3252_paris_download_scope import prepare, SIGNED
from tests.test_p1_3252_paris_selected_right_freshness import raw_loan


@pytest.mark.parametrize("url", [
    "https://bibliotheques.paris.fr/numerique/Portal/ASSARedirect.ashx?url=token",
    "https://bibliotheques.paris.fr/numerique/Portal/ASSA%52edirect.ashx?url=token",
    "https://bibliotheques.paris.fr/numerique/Portal/ASSA%2552edirect.ashx?url=token",
    "https://bibliotheques.paris.fr/numerique/Portal/ASSA%252552edirect.ashx?url=token",
    "https://bibliotheques.paris.fr/numerique/DownloadDigitalDocument?book=edition-1",
    "https://bibliotheques.paris.fr/numerique/Download%2544igitalDocument?book=edition-1",
])
def test_selected_existing_right_refuses_known_mutating_url_before_get(
    tmp_path, monkeypatch, url,
):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan(url)]
    try:
        with pytest.raises(
            ProviderConfigurationError,
            match="paris_selected_delivery_mutating_redirect_denied",
        ):
            session.download_digital(
                loan, account, tmp_path, expected_right_id="loan-1",
                expected_identity_id="identity-A",
            )
        assert content_gets == []
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


@pytest.mark.parametrize("endpoint", [
    "ASSARedirect.ashx",
    "ASSA%2552edirect.ashx",
    "Download%2544igitalDocument",
])
def test_selected_redirect_cannot_issue_second_get_to_mutating_path(
    tmp_path, monkeypatch, endpoint,
):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan(SIGNED)]
    origin_get = client._get_pnb_response
    def redirect(url, **kwargs):
        content_gets.append(str(url))
        return httpx.Response(
            302, headers={"location": (
                "/numerique/Portal/" + endpoint + "?url="
                + quote("/synthetic.acsm", safe="")
            )},
            request=httpx.Request("GET", str(url)),
        )
    client._get_pnb_response = redirect
    try:
        with pytest.raises(
            ProviderConfigurationError,
            match="paris_selected_delivery_mutating_redirect_denied",
        ):
            session.download_digital(
                loan, account, tmp_path, expected_right_id="loan-1",
                expected_identity_id="identity-A",
            )
        assert len(content_gets) == 1
        assert not list(tmp_path.iterdir())
    finally:
        session.close()

@pytest.mark.parametrize("url", [
    "https://bibliotheques.paris.fr/numerique/Portal/ASSA%2552edirect.ashx",
    "https://bibliotheques.paris.fr/numerique/DownloadDigitalDocument",
    "https://bibliotheques.paris.fr/numerique/Download%2544igitalDocument",
])
def test_first_acsm_read_never_replays_mutating_endpoint(
    tmp_path, monkeypatch, url,
):
    session, account, loan, client, content_gets = prepare(tmp_path, monkeypatch)
    loan.ebook_url = url
    try:
        with pytest.raises(
            ProviderConfigurationError, match="paris_initial_acsm_replay_denied",
        ):
            session.download_digital(
                loan, account, tmp_path,
                initial_offer_only=True, admission_current=lambda: True,
            )
        assert content_gets == []
        assert not list(tmp_path.iterdir())
    finally:
        session.close()
