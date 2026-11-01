"""#2055: distinguish synthetic BnF search HTTP failures without browser/SSO."""
import httpx
import pytest

from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError, allowed_url
from cifereca_bnf_pret_catalog import query_url


@pytest.mark.parametrize(("status", "expected"), [
    (401, "auth_required"),
    (403, "access_denied"),
    (404, "http_unavailable"),
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
        assert request.url.path == "/resources"
        return httpx.Response(status, headers={"content-type": "text/html"})

    client = ReaderHTTPClient(transport=httpx.MockTransport(handler),
                              diagnostics=Diagnostics())
    try:
        with pytest.raises(ReaderHTTPError, match="^" + expected + "$"):
            client.request("GET", "https://pret.bnf.fr/resources?q=Ewilan")
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
            client.request("GET", "https://pret.bnf.fr/resources?q=Ewilan")
    finally:
        client.close()


def test_user_observed_resources_search_route_and_legacy_route_refused():
    url = query_url("révolte")
    assert url == "https://pret.bnf.fr/resources?q=r%C3%A9volte"
    assert allowed_url(url) == url
    assert allowed_url("https://pret.bnf.fr/resources/658190864c485400011bc289")
    with pytest.raises(ReaderHTTPError, match="^url_refused$"):
        allowed_url("https://pret.bnf.fr/results?q=r%C3%A9volte")


def test_current_search_route_generates_redacted_search_diagnostics():
    events = []

    class Diagnostics:
        def add(self, category, operation, result, **kwargs):
            events.append(kwargs.get("detail", {}))

    def handler(request):
        assert request.url.path == "/resources"
        assert request.url.params.get("q") == "révolte"
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    client = ReaderHTTPClient(transport=httpx.MockTransport(handler), diagnostics=Diagnostics())
    try:
        result_url, _html = client.request("GET", query_url("révolte"))
        assert result_url == "https://pret.bnf.fr/resources?q=r%C3%A9volte"
    finally:
        client.close()
    response = [item for item in events if item.get("event") == "response"]
    assert len(response) == 1
    assert response[0]["logical_endpoint"] == "search"
    assert response[0]["phase"] == "catalog_search"
    assert response[0]["http_status"] == 200
    assert "révolte" not in str(events)
