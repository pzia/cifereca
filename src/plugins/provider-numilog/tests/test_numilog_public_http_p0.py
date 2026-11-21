"""#1886: anonymous public search transport and owned/public safety boundary.

The response is the sanitized user-provided Jefferson fixture; no live requests.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Event
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


def _overlapping_readers(monkeypatch, response, first, second):
    """Both callers reach the same in-flight transport before it returns."""
    fake, _ = setup_public_client(monkeypatch, response)
    started, joined, release = Event(), Event(), Event()
    original_wait = public_client._await_public_search_flight

    @contextmanager
    def stream(method, url):
        fake.requests.append((method, url))
        started.set()
        assert release.wait(5)
        yield response

    def await_flight(future, timeout):
        joined.set()
        return original_wait(future, timeout)

    monkeypatch.setattr(fake, "stream", stream)
    monkeypatch.setattr(public_client, "_await_public_search_flight", await_flight)
    with ThreadPoolExecutor(max_workers=2) as workers:
        leader = workers.submit(first)
        try:
            assert started.wait(5)
            follower = workers.submit(second)
            assert joined.wait(5)
        finally:
            release.set()
    return fake, leader, follower


def test_overlapping_exact_public_get_parses_once_and_receipts_are_independent(monkeypatch):
    original_parse = public_client.parse_public_search
    parses = []

    def parse(text, url):
        parses.append(url)
        return original_parse(text, url)

    monkeypatch.setattr(public_client, "parse_public_search", parse)
    def read():
        return public_client.fetch_public_search("jefferson", _retain_input=True)
    fake, first, second = _overlapping_readers(monkeypatch, FakeResponse(), read, read)
    left, right = first.result(), second.result()
    assert len(fake.requests) == len(parses) == 1
    assert len(left.page.items) == len(right.page.items) == 20
    assert left.page.items[0] is not right.page.items[0]
    assert left.raw_input.token != right.raw_input.token
    assert left.raw_input.transport_digest == right.raw_input.transport_digest
    assert left.raw_input.input_digest == right.raw_input.input_digest
    assert all(not row.owned and not row.downloadable for row in right.page.items)
    assert not public_client._PUBLIC_SEARCH_FLIGHTS


def test_overlapping_public_error_is_shared_without_retry_or_false_zero(monkeypatch):
    response = FakeResponse(status=429)
    response.headers["retry-after"] = "120"
    def read():
        return public_client.fetch_public_search("jefferson")
    fake, first, second = _overlapping_readers(monkeypatch, response, read, read)
    for result in first, second:
        with pytest.raises(public_client.PublicRateLimitError) as error:
            result.result()
        assert str(error.value) == "Numilog : public_catalog_rate_limited"
        assert error.value.retry_after_seconds == 120
    assert len(fake.requests) == 1
    assert not public_client._PUBLIC_SEARCH_FLIGHTS
    # Not a second TTL cache: the independent ordinary transport call can
    # run again; session-owned cooldown remains separately enforced.
    with pytest.raises(public_client.PublicRateLimitError):
        read()
    assert len(fake.requests) == 2


@pytest.mark.parametrize("guarded", [False, True])
def test_different_page_or_explicit_refresh_never_joins_public_flight(monkeypatch, guarded):
    response = FakeResponse()
    fake, _ = setup_public_client(monkeypatch, response)
    first_started, second_started, release = Event(), Event(), Event()
    checked = []
    original_url = public_client.public_search_url("jefferson")

    @contextmanager
    def stream(method, url):
        fake.requests.append((method, url))
        (first_started if len(fake.requests) == 1 else second_started).set()
        assert release.wait(5)
        yield response

    monkeypatch.setattr(fake, "stream", stream)

    def second():
        if guarded:
            return public_client.fetch_public_search_url(
                original_url, _before_get=lambda: checked.append(True))
        return public_client.fetch_public_search("jefferson", page=1)

    with ThreadPoolExecutor(max_workers=2) as workers:
        leader = workers.submit(public_client.fetch_public_search, "jefferson")
        try:
            assert first_started.wait(5)
            follower = workers.submit(second)
            assert second_started.wait(5)
        finally:
            release.set()
    assert len(leader.result().items) == 20
    if guarded:
        assert len(follower.result().items) == 20
        assert checked == [True]
        assert fake.requests[0] == fake.requests[1]
    else:
        # Page-zero fixture is deliberately not valid evidence for page 1.
        with pytest.raises(ProviderError, match="public_catalog_layout_unverified"):
            follower.result()
        assert fake.requests[0] != fake.requests[1]
    assert len(fake.requests) == 2
    assert not public_client._PUBLIC_SEARCH_FLIGHTS


def test_live_and_replay_layout_reason_is_closed_and_identical(monkeypatch):
    body = b"<form>Login</form>"
    url = public_client.public_search_url("jefferson")
    fake, _ = setup_public_client(monkeypatch, FakeResponse(body=body))
    raw = public_client._retained_search_input(body, url)
    for read in (lambda: public_client.fetch_public_search("jefferson"),
                 lambda: public_client._parse_retained_search_input(raw)):
        with pytest.raises(ProviderError) as error:
            read()
        assert str(error.value) == "Numilog : public_catalog_layout_unverified:unknown_search_layout"
    assert len(fake.requests) == 1

    def hostile_reason(*_):
        raise public_client.PublicSearchParseError("private-marker")
    monkeypatch.setattr(public_client, "parse_public_search", hostile_reason)
    with pytest.raises(ProviderError) as error:
        public_client._parse_retained_search_input(raw)
    assert str(error.value) == "Numilog : public_catalog_layout_unverified:unknown"
