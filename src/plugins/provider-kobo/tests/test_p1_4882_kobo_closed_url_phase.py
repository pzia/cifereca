"""#4882: K1 Core URL refusal phase propagated as privacy-safe plugin events.

The real Core HTTPX download service is invoked. No private URLs enter
the log, no live network and no file is published on denied K1 flows.
"""
import logging

import httpx
import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError
from cifereca_kobo_selected_candidate import _closed_url_refusal
from tests.test_p1_3252_kobo_selected_candidate import (
    admitted, execute, fake_public_client,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import SIGNED, setup
from tests.test_p1_4882_kobo_httpx_redirect_integration import _session_with_k1


def _events(caplog, part):
    return [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith(part)
    ]


def test_k1_initial_signed_url_invalid_cannot_consume_get(
    monkeypatch, tmp_path, caplog,
):
    session, vault, _broker, account, posts = setup()
    orig = vault.acquisition
    def with_trailing_fragment(product_id, **kwargs):
        result = orig(product_id, **kwargs)
        result["acquisition"]["candidates"][0]["url"] = SIGNED + "#"
        return result
    vault.acquisition = with_trailing_fragment
    requested = []
    fake_public_client(
        monkeypatch,
        lambda request: requested.append(request)
        or pytest.fail("no GET with invalid initial URL"), [],
    )
    try:
        offered = admitted(session, account)
        assert offered and requested == []
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            with pytest.raises(
                ProviderCapabilityError, match="^kobo_candidate_redirect_refused$",
            ):
                execute(session, account, offered, tmp_path)
        assert _events(caplog, "EVENT kobo candidate_http_url_refusal ") == [
            "EVENT kobo candidate_http_url_refusal "
            "phase=initial category=fragment_marker"
        ]
        assert _events(caplog, "EVENT kobo candidate_http_boundary ") == [
            "EVENT kobo candidate_http_boundary cause=delivery_url_invalid"
        ]
        assert requested == []
        assert len(posts) == 2
        assert list(tmp_path.iterdir()) == []
        assert SIGNED not in caplog.text
    finally:
        session.close()


def test_k1_redirect_signed_url_invalid_cannot_consume_second_get(
    monkeypatch, tmp_path, caplog,
):
    session, account, posts = _session_with_k1()
    seen = []
    private_redirect = (
        "https://cdn-receipt.example/receipt.epub"
        "?signature=SYNTHETIC_REDIRECT_PRIVATE#SYNTHETIC_PRIVATE_FRAGMENT"
    )
    def handler(request):
        seen.append(str(request.url))
        assert str(request.url) == SIGNED
        return httpx.Response(302, headers={"location": private_redirect})

    fake_public_client(monkeypatch, handler, [])
    try:
        offered = admitted(session, account)
        assert offered
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            with pytest.raises(
                ProviderCapabilityError, match="^kobo_candidate_redirect_refused$",
            ):
                execute(session, account, offered, tmp_path)
        assert seen == [SIGNED]
        assert len(posts) == 2
        assert not list(tmp_path.iterdir())
        assert _events(caplog, "EVENT kobo candidate_http_url_refusal ") == [
            "EVENT kobo candidate_http_url_refusal "
            "phase=redirect category=fragment_marker"
        ]
        assert _events(caplog, "EVENT kobo candidate_http_boundary ") == [
            "EVENT kobo candidate_http_boundary cause=delivery_url_invalid"
        ]
        for private in ("SYNTHETIC_REDIRECT_PRIVATE", SIGNED, "https://"):
            assert private not in caplog.text
    finally:
        session.close()


def test_closed_logger_refuses_untrusted_categories(caplog):
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        _closed_url_refusal("initial", "fragment_marker")
        _closed_url_refusal("https://private.invalid/", "fragment_marker")
        _closed_url_refusal("initial", "url=PRIVATE_SECRET")
        _closed_url_refusal("redirect", "SYNTHETIC_PRIVATE")
    assert _events(caplog, "EVENT kobo candidate_http_url_refusal ") == [
        "EVENT kobo candidate_http_url_refusal "
        "phase=initial category=fragment_marker"
    ]
    assert "PRIVATE" not in caplog.text
