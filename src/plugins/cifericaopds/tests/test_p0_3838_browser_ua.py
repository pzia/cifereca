"""#3838: CifericaOPDS read-only HTTP inherits the Core browser identity."""
import httpx
import pytest

import cifereca_cifericaopds as opds
from cifereca.http_trace import BROWSER_USER_AGENT


def test_candidate_get_inherits_browser_ua_without_changing_payload_headers(monkeypatch):
    original = opds.HTTPX_TRANSPORT.client
    observed = []

    def respond(request):
        observed.append(request)
        return httpx.Response(
            200, stream=httpx.ByteStream(b'{"api_major": 1, "sources": []}'),
            headers={"content-type": "application/json"},
        )

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(opds.HTTPX_TRANSPORT, "client", client)
    assert opds.JsonClient("https://opds.example.test").get("/api/v1/sources") == {
        "api_major": 1, "sources": [],
    }
    assert len(observed) == 1
    assert observed[0].headers["User-Agent"] == BROWSER_USER_AGENT
    assert observed[0].headers["Accept-Encoding"] == "identity"
    assert observed[0].headers.get("cookie") is None


def test_candidate_redirect_remains_refused_without_followup(monkeypatch):
    original = opds.HTTPX_TRANSPORT.client
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            302, headers={"location": "https://other.example.test/private"},
        )
    monkeypatch.setattr(
        opds.HTTPX_TRANSPORT, "client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(opds.ContractUnavailable, match="redirect refused"):
        opds.JsonClient("https://opds.example.test").get("/api/v1/sources")
    assert len(requests) == 1
    assert requests[0].headers["User-Agent"] == BROWSER_USER_AGENT
