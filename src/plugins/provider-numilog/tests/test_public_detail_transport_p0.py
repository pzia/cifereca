"""No-network transport tests for explicitly requested Numilog public details."""
import json
import httpx
import pytest

import cifereca_numilog_public_client as public_client
from cifereca.plugin_api import ProviderCapabilityError, ProviderError

URL = "https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook"
BODY = ('<script type="application/ld+json">'
        + json.dumps({"@type": "Book", "url": URL, "name": "Fixture"}) + '</script>')


class AnonymousClient:
    requests = []

    def __init__(self, **kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert "Cookie" not in kwargs["headers"]
        self.__class__.requests.append(("client", kwargs))
        self.status = 200
        self.body = BODY
        self.mime = "text/html"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def stream(self, method, url):
        assert method == "GET"
        assert url == URL
        self.__class__.requests.append(("request", url))
        return self

    @property
    def status_code(self):
        return self.status

    @property
    def headers(self):
        return {"content-type": self.mime}

    def iter_bytes(self):
        yield self.body.encode("utf-8")


def test_anonymous_detail_fetch_never_uses_private_session(monkeypatch):
    AnonymousClient.requests = []
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", AnonymousClient)
    result = public_client.fetch_public_detail(URL)
    assert result["fields"]["title"] == "Fixture"
    assert not result["owned"] and not result["downloadable"]
    assert len([r for r in AnonymousClient.requests if r[0] == "request"]) == 1


@pytest.mark.parametrize("url", [
    URL + "?token=PRIVATE_MARKER",
    "https://evil.example/1958476/book.ebook",
    "https://www.numilog.com/Pages/Compte/MesLivres.aspx",
])
def test_bad_url_does_not_open_http_client(monkeypatch, url):
    def forbidden(**kwargs):
        raise AssertionError("network must not start")
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", forbidden)
    with pytest.raises(ProviderCapabilityError):
        public_client.fetch_public_detail(url)


@pytest.mark.parametrize("status,code", [
    (403, "forbidden"), (429, "rate_limited"),
    (302, "redirect_unverified"), (503, "unavailable"),
])
def test_http_failures_are_typed_without_fallback(monkeypatch, status, code):
    class Fail(AnonymousClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.status = status
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", Fail)
    with pytest.raises(ProviderError, match="public_detail_" + code):
        public_client.fetch_public_detail(URL)


def test_unknown_html_is_not_treated_as_empty_metadata(monkeypatch):
    class Login(AnonymousClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.body = "<form><input type='password' value='PRIVATE_MARKER'></form>"
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", Login)
    with pytest.raises(ProviderError, match="public_detail_layout_unverified") as caught:
        public_client.fetch_public_detail(URL)
    assert "PRIVATE_MARKER" not in str(caught.value)


def test_large_body_is_rejected_before_parse(monkeypatch):
    class Large(AnonymousClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.body = "a" * (1024 * 1024 + 1)
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", Large)
    with pytest.raises(ProviderError, match="public_detail_response_too_large"):
        public_client.fetch_public_detail(URL)
