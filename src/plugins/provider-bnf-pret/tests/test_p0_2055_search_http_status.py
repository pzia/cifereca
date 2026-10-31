"""#2055: distinguish synthetic BnF search HTTP failures without browser/SSO."""
import httpx
import pytest

from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError


@pytest.mark.parametrize(("status", "expected"), [
    (401, "auth_required"),
    (403, "access_denied"),
    (429, "rate_limited"),
    (500, "http_unavailable"),
    (503, "http_unavailable"),
])
def test_search_failure_code_preserves_http_status_in_diagnostics(status, expected):
    events = []

    class Diagnostics:
        def add(self, category, operation, result, **kwargs):
            events.append((category, operation, result, kwargs.get("detail", {})))

    def handler(request):
        assert request.url.host == "pret.bnf.fr"
        assert request.url.path == "/results"
        return httpx.Response(status, headers={"content-type": "text/html"})

    client = ReaderHTTPClient(transport=httpx.MockTransport(handler),
                              diagnostics=Diagnostics())
    try:
        with pytest.raises(ReaderHTTPError, match="^" + expected + "$"):
            client.request("GET", "https://pret.bnf.fr/results?q=Ewilan")
    finally:
        client.close()
    response = [detail for _, _, _, detail in events
                if detail.get("event") == "response"]
    assert len(response) == 1
    assert response[0]["http_status"] == status
    assert all("Ewilan" not in str(detail) for _, _, _, detail in events)


def test_search_transport_error_does_not_become_a_false_http_status():
    def handler(request):
        raise httpx.ConnectError("synthetic network failure")

    client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ReaderHTTPError, match="^transport_unavailable$"):
            client.request("GET", "https://pret.bnf.fr/results?q=Ewilan")
    finally:
        client.close()
