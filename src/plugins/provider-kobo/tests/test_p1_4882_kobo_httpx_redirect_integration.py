"""#4882: Kobo K1 exact-EPUB staging through Core's REAL HTTPS redirect service.

No live internet, no KVault network, no signed credentials: only HTTPX
MockTransport with the production delivery service and plugin guards.
"""
from copy import deepcopy
import logging

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_candidate import (
    admitted, execute, epub_bytes, fake_public_client,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    SIGNED, descriptor, setup,
)


PDF_URL = "https://cdn.kobo.com/alternate/book.pdf?signature=SYNTHETIC_PDF"
CDN_URL = "https://cdn-for-epub.example/receipt.epub?token=SECRET_CDN"


def _two_route_k1():
    envelope = descriptor()
    original = envelope["acquisition"]["candidates"][0]
    pdf = deepcopy(original)
    pdf.update(media_format="pdf", url=PDF_URL)
    envelope["acquisition"]["candidates"] = [pdf, original]
    return envelope


def _session_with_k1():
    session, vault, _broker, account, posts = setup()
    original = vault.acquisition

    def only_k1(product, **kwargs):
        response = original(product, **kwargs)
        response["acquisition"]["candidates"] = deepcopy(
            _two_route_k1()["acquisition"]["candidates"]
        )
        return response

    vault.acquisition = only_k1
    return session, account, posts


@pytest.mark.parametrize("redirect_status", [302, 307, 308])
@pytest.mark.parametrize("request_payer", [False, True])
def test_exact_k1_epub_follows_public_https_redirect_without_secret_leak(
    tmp_path, monkeypatch, caplog, redirect_status, request_payer,
):
    session, account, posts = _session_with_k1()
    # Upstream declares ONLY Kobo's bounded non-authenticating S3 marker.
    original = session.client.acquisition

    def with_header(product, **kwargs):
        result = original(product, **kwargs)
        if request_payer:
            for item in result["acquisition"]["candidates"]:
                if item["media_format"] == "epub":
                    item["headers"] = {"x-amz-request-payer": "requester"}
        return result

    session.client.acquisition = with_header
    payload = epub_bytes()
    requests = []
    created = []

    def handler(request):
        requests.append(request)
        if str(request.url) == SIGNED:
            return httpx.Response(
                redirect_status,
                headers={
                    "location": CDN_URL,
                    "set-cookie": "test_session=SECRET_COOKIE; Path=/",
                },
            )
        assert str(request.url) == CDN_URL
        return httpx.Response(
            200, content=payload,
            headers={"content-type": "application/epub+zip"},
        )

    fake_public_client(monkeypatch, handler, created)
    try:
        selected = admitted(session, account)
        assert selected.startswith("kobo.descriptor:")
        assert requests == []
        with caplog.at_level(logging.INFO, logger="cifereca"):
            target, reference = execute(session, account, selected, tmp_path)
        assert target.read_bytes() == payload and target.suffix == ".epub"
        assert reference == ""
        assert [str(request.url) for request in requests] == [SIGNED, CDN_URL]
        assert len(posts) == 2
        assert created == [("provider.kobo.delivery", 30)]
        for req in requests:
            assert "authorization" not in req.headers
            assert "cookie" not in req.headers
        assert ("x-amz-request-payer" in requests[0].headers) is request_payer
        assert ("x-amz-request-payer" in requests[1].headers) is request_payer
        if request_payer:
            assert requests[1].headers["x-amz-request-payer"] == "requester"
        assert "SECRET_CDN" not in caplog.text
        assert "SECRET_COOKIE" not in caplog.text
        assert "SYNTHETIC_PRIVATE_SIGNATURE" not in caplog.text
        assert not any("candidate_http_boundary" in r.getMessage()
                       for r in caplog.records)
    finally:
        session.close()


@pytest.mark.parametrize(("location", "code"), [
    (None, "delivery_redirect_missing"),
    ("http://cdn-for-epub.example/receipt.epub?token=SECRET_CDN",
     "delivery_url_invalid"),
    ("https://127.0.0.1/receipt.epub?token=SECRET_CDN",
     "delivery_ip_literal_refused"),
    ("javascript:alert(SECRET_CDN)", "delivery_redirect_invalid"),
])
def test_invalid_real_httpx_redirect_logs_closed_cause_and_never_gets_target(
    tmp_path, monkeypatch, caplog, location, code,
):
    session, account, posts = _session_with_k1()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert str(request.url) == SIGNED, "invalid redirect must not GET target"
        headers = {} if location is None else {"location": location}
        return httpx.Response(302, headers=headers)

    fake_public_client(monkeypatch, handler, [])
    try:
        offered = admitted(session, account)
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            with pytest.raises(
                ProviderCapabilityError, match="^kobo_candidate_redirect_refused$",
            ):
                execute(session, account, offered, tmp_path)
        assert calls == [SIGNED]
        assert len(posts) == 2
        assert not list(tmp_path.iterdir())
        events = [
            record.getMessage() for record in caplog.records
            if record.getMessage().startswith(
                "EVENT kobo candidate_http_boundary "
            )
        ]
        assert events == [
            f"EVENT kobo candidate_http_boundary cause={code}"
        ]
        for secret in (
            "SECRET_CDN", "SECRET_COOKIE", "SYNTHETIC_PRIVATE_SIGNATURE",
            "https://", "127.0.0.1",
        ):
            assert secret not in caplog.text
    finally:
        session.close()
