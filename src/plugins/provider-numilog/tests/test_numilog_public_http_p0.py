"""#1886: anonymous public search transport and owned/public safety boundary.

The response is the sanitized user-provided Jefferson fixture; no live requests.
"""
from contextlib import contextmanager
from pathlib import Path

import pytest
import json

from cifereca.plugin_api import ProviderError, ProviderCapabilityError
import cifereca_numilog_public_client as public_client
from cifereca_numilog import NumilogSession, DESCRIPTOR, VERSION

HTML = (Path(__file__).parent / "fixtures" /
        "public_search_jefferson_20261016.html").read_bytes()


class FakeResponse:
    def __init__(self, *, status=200, body=HTML, content_type="text/html; charset=utf-8"):
        self.status_code = status
        self.body = body
        self.headers = {"content-type": content_type}

    def iter_bytes(self):
        # Bounded streaming must handle multiple chunks and a large response.
        for start in range(0, len(self.body), 1024):
            yield self.body[start:start + 1024]


class PublicClient:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    @contextmanager
    def stream(self, method, url):
        self.requests.append((method, url))
        yield self.response


def setup_public_client(monkeypatch, response=None):
    fake = PublicClient(response or FakeResponse())
    parameters = []
    def factory(**kwargs):
        parameters.append(kwargs)
        return fake
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", factory)
    return fake, parameters


def test_public_catalogue_scope_returns_20_non_owned_books_without_private_call(monkeypatch):
    # Build the private client *before* intercepting public-only construction.
    session = NumilogSession(
        credential_id="fictional-account", label="", credentials={},
        options={}, config={}, diagnostics=None,
    )
    session._rows = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("private library was read")
    )
    session.connected = False
    fake, parameters = setup_public_client(monkeypatch)
    try:
        rows = session.search("jefferson", scope="catalog", page=0, field="all")
        assert len(rows) == 20
        assert rows[0].title == "Jefferson prend des risques"
        assert rows[0].price == "9.99" and rows[0].currency == "EUR"
        assert rows[0].metadata["price"]["minor_units"] == 999
        assert len({r.record_id for r in rows}) == 20
        assert all(r.owned is False and r.downloadable is False
                   and r.download_url is None and r.source_collection == "public_catalog"
                   and r.source_credential == "__public__" for r in rows)
        assert len(fake.requests) == 1
        assert fake.requests[0] == ("GET", public_client.public_search_url("jefferson"))
        assert parameters[0]["follow_redirects"] is False
        assert parameters[0]["trust_env"] is False
        assert "cookies" not in parameters[0] and "Authorization" not in parameters[0]["headers"]
        assert "Cookie" not in parameters[0]["headers"]
    finally:
        session.close()


@pytest.mark.parametrize("field", ("author", "isbn", "title", "language", ""))
def test_unqualified_public_search_fields_are_never_sent(monkeypatch, field):
    fake, _ = setup_public_client(monkeypatch)
    with pytest.raises(ProviderCapabilityError, match="unsupported_public_field"):
        public_client.fetch_public_search("jefferson", field=field)
    assert fake.requests == []


@pytest.mark.parametrize("query,page", (
    ("x", 0), ("\nprivate-marker", 0), ("normal", 101),
    ("normal", -1), ("normal", True), ("normal", "2"),
))
def test_untrusted_query_and_pagination_rejected_before_network(monkeypatch, query, page):
    fake, _ = setup_public_client(monkeypatch)
    with pytest.raises(ProviderCapabilityError):
        public_client.fetch_public_search(query, page=page)
    assert fake.requests == []


@pytest.mark.parametrize("status,message", (
    (403, "public_catalog_forbidden"), (401, "public_catalog_forbidden"),
    (429, "public_catalog_rate_limited"), (302, "public_catalog_redirect_unverified"),
    (500, "public_catalog_unavailable"),
))
def test_http_failures_never_retry_with_account_cookies(monkeypatch, status, message):
    fake, parameters = setup_public_client(monkeypatch, FakeResponse(status=status))
    with pytest.raises(ProviderError, match=message):
        public_client.fetch_public_search("jefferson")
    assert len(fake.requests) == len(parameters) == 1


def test_unverified_login_html_does_not_count_as_empty_catalogue(monkeypatch):
    fake, _ = setup_public_client(monkeypatch, FakeResponse(body=b"<form>Login</form>"))
    with pytest.raises(ProviderError, match="public_catalog_layout_unverified"):
        public_client.fetch_public_search("jefferson")
    assert len(fake.requests) == 1


def test_unexpected_content_type_and_oversized_body_are_rejected(monkeypatch):
    fake, _ = setup_public_client(monkeypatch, FakeResponse(content_type="application/json"))
    with pytest.raises(ProviderError, match="public_catalog_unexpected_mime"):
        public_client.fetch_public_search("jefferson")
    assert len(fake.requests) == 1

    fake, _ = setup_public_client(
        monkeypatch, FakeResponse(body=b"x" * (public_client.MAX_HTML_BYTES + 1)),
    )
    with pytest.raises(ProviderError, match="public_catalog_response_too_large"):
        public_client.fetch_public_search("jefferson")
    assert len(fake.requests) == 1


def test_second_page_uses_observed_grid_page_parameter_not_postback():
    url = public_client.public_search_url("jefferson", page=2)
    assert url.endswith("mode=brSimple&query=jefferson&GRID_PAGE=2")
    assert "__VIEWSTATE" not in url and "auth=" not in url


def test_public_catalogue_is_declared_separately_and_release_version_matches():
    cap = DESCRIPTOR.search_capabilities
    assert cap.declared
    assert cap.fields == ("all", "title")
    assert cap.scopes == ("all", "digital", "catalog")
    assert cap.supports_scope("catalog")
    assert not cap.supports_field("isbn")
    # The manifest, not a test literal, owns the package version.
    manifest = json.loads((Path(__file__).parents[1] / "cifereca-plugin.json").read_text())
    assert manifest["package"]["version"] == VERSION
    assert manifest["plugins"][0]["version"] == VERSION
    assert "catalog.search" in manifest["plugins"][0]["capabilities"]
