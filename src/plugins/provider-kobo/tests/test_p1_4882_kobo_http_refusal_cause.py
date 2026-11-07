"""P1 #4882: anonymous, closed Kobo HTTPS refusal diagnostics.

All requests, offers and signer strings are synthetic. No real account,
provider traffic, staging publication, Core/HTTP transport modification.
"""
import logging

import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_delivery_offer import SIGNED, setup
from tests.test_p1_3252_kobo_selected_candidate import admitted, execute


@pytest.mark.parametrize("refusal", [
    "delivery_redirect_invalid",
    "delivery_redirect_missing",
    "delivery_url_invalid",
    "delivery_ip_literal_refused",
])
def test_kobo_http_boundary_reports_closed_internal_cause_without_secrets(
    monkeypatch, caplog, tmp_path, refusal,
):
    session, _vault, _broker, account, posts = setup()
    calls = []

    def refusal_in_core(**kwargs):
        calls.append(kwargs["producer"])
        assert kwargs["url"] == SIGNED
        raise ValueError(refusal)

    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download", refusal_in_core,
    )
    try:
        offer = admitted(session, account)
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            with pytest.raises(
                ProviderCapabilityError,
                match="^kobo_candidate_redirect_refused$",
            ) as exc:
                execute(session, account, offer, tmp_path)

        events = [
            record.getMessage() for record in caplog.records
            if record.getMessage().startswith(
                "EVENT kobo candidate_http_boundary "
            )
        ]
        assert events == [
            f"EVENT kobo candidate_http_boundary cause={refusal}"
        ]
        assert calls == ["provider.kobo.delivery"]
        assert len(posts) == 2
        assert not list(tmp_path.iterdir())
        assert "PRIVATE" not in str(events)
        assert "SYNTHETIC_PRIVATE_SIGNATURE" not in str(events)
        assert "https://" not in str(events)
        assert "https://" not in str(exc.value)
        assert refusal not in str(exc.value)  # ABI remains unchanged.
    finally:
        session.close()


@pytest.mark.parametrize("untrusted", [
    "delivery_http_status",
    "delivery_redirect_invalid?location=https://DO_NOT_EXPOSE.example",
    "delivery_redirect_invalid\nhttps://DO_NOT_EXPOSE.example",
])
def test_other_or_injected_transport_exception_never_leaks_private_text(
    monkeypatch, caplog, tmp_path, untrusted,
):
    session, _vault, _broker, account, _posts = setup()

    def injected(**kwargs):
        raise ValueError(untrusted)

    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download", injected,
    )
    try:
        offer = admitted(session, account)
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            with pytest.raises(ProviderCapabilityError) as exc:
                execute(session, account, offer, tmp_path)

        assert not any(
            record.getMessage().startswith(
                "EVENT kobo candidate_http_boundary "
            )
            for record in caplog.records
        )
        assert "DO_NOT_EXPOSE" not in str(exc.value)
        assert "https://" not in str(exc.value)
        assert not list(tmp_path.iterdir())
    finally:
        session.close()
