"""#3724/#4982: replay the exact admitted anonymous transport, no second GET."""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
import cifereca_numilog_public_client as client
import cifereca_numilog_public_provider as provider
from cifereca_numilog_public_cache import PublicCatalogueCache, TTL_SECONDS

BODY = (Path(__file__).parent / "fixtures" /
        "public_search_jefferson_20261016.html").read_bytes()


def fixture(monkeypatch, *, status=200):
    requests = []

    class Response:
        status_code = status
        headers = {"content-type": "text/html; charset=utf-8"}

        def iter_bytes(self):
            for start in range(0, len(BODY), 1024):
                yield BODY[start:start + 1024]

    class Transport:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        @contextmanager
        def stream(self, method, url):
            requests.append((method, url))
            yield Response()

    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", lambda **kwargs: Transport())
    return requests


def session(*, enabled=True):
    s = provider.NumilogPublicSession(
        credential_id="__public__", label="", credentials={}, options={},
        _retain_public_search_inputs=enabled,
    )
    clock = [100.0]
    s._public_cache = PublicCatalogueCache(clock=lambda: clock[0])
    s.connect()
    return s, clock


def test_same_get_exact_bytes_and_offline_reparse(monkeypatch):
    requests = fixture(monkeypatch)
    s, clock = session()
    try:
        rows, token = s._search_with_retained_input("jefferson")
        assert len(rows) == 20 and token is not None
        raw, ttl = s._read_retained_public_input(token)
        assert raw._body == BODY and raw.input_digest == sha256(BODY).hexdigest()
        assert ttl == TTL_SECONDS
        assert "jefferson" not in repr(raw)
        assert len(requests) == 1
        expected = rows[0]
        assert s._reparse_retained_public_row(token, expected.record_id) == expected
        assert s.search("jefferson")[0] == expected
        cached, same_token = s._search_with_retained_input("jefferson")
        assert cached == rows and same_token == token
        assert s._reparse_retained_public_row(token, "foreign") is None
        assert len(requests) == 1
        assert all(not row.owned and not row.downloadable and row.download_url is None for row in rows)
        clock[0] += TTL_SECONDS
        assert s._read_retained_public_input(token) == (None, 0)
        assert s._reparse_retained_public_row(token, expected.record_id) is None
        assert len(requests) == 1
        _, refreshed = s._search_with_retained_input("jefferson")
        assert refreshed != token and len(requests) == 2
    finally:
        s.close()


def test_restart_and_reconnect_fence_old_inputs(monkeypatch):
    requests = fixture(monkeypatch)
    s, _ = session()
    try:
        rows, token = s._search_with_retained_input("jefferson")
        original = client.parse_public_search

        def reconnect(*args):
            s.close()
            s.connect()
            return original(*args)

        monkeypatch.setattr(client, "parse_public_search", reconnect)
        with pytest.raises(ProviderError, match="navigation_invalidated"):
            s._reparse_retained_public_row(token, rows[0].record_id)
        assert s._read_retained_public_input(token) == (None, 0)
        assert len(requests) == 1
    finally:
        s.close()
    other, _ = session()
    try:
        assert other._read_retained_public_input(token) == (None, 0)
        assert len(requests) == 1
    finally:
        other.close()


def test_projection_only_and_large_input_do_not_enable_replay(monkeypatch):
    requests = fixture(monkeypatch)
    s, _ = session(enabled=False)
    try:
        assert len(s.search("jefferson")) == 20
        with pytest.raises(ProviderCapabilityError, match="replay_scope_forbidden"):
            s._search_with_retained_input("jefferson")
        assert len(requests) == 1
    finally:
        s.close()
    monkeypatch.setattr(client, "_MAX_RETAINED_BYTES", 1)
    s, _ = session()
    try:
        rows, token = s._search_with_retained_input("jefferson")
        assert len(rows) == 20 and token is None
        assert s.search("jefferson") == list(rows)
        assert len(requests) == 2
    finally:
        s.close()


def test_parser_coordinate_change_and_corruption_refused_without_transport(monkeypatch):
    requests = fixture(monkeypatch)
    s, _ = session()
    try:
        rows, token = s._search_with_retained_input("jefferson")
        raw, _ = s._read_retained_public_input(token)
        for corrupt in (replace(raw, _body=b"other"), replace(raw, transport_digest="0"*64)):
            with pytest.raises(ProviderError, match="replay_invalid"):
                client._parse_retained_search_input(corrupt)
        original = client.parse_public_search

        def changed(*args):
            page = original(*args)
            return replace(page, items=(replace(page.items[0], docbase="OTHER"), *page.items[1:]))

        monkeypatch.setattr(client, "parse_public_search", changed)
        with pytest.raises(ProviderError, match="coordinate_changed"):
            s._reparse_retained_public_row(token, rows[0].record_id)
        assert len(requests) == 1
    finally:
        s.close()


def test_commit_guard_serializes_reconnect_without_holding_network(monkeypatch):
    requests = fixture(monkeypatch)
    s, _ = session()
    started, closed = Event(), Event()
    thread = None
    try:
        _rows, token = s._search_with_retained_input("jefferson")
        raw, _ = s._read_retained_public_input(token)

        def reconnect():
            started.set()
            s.close()
            s.connect()
            closed.set()

        with s._hold_retained_public_input(token) as held:
            assert held == raw
            # A second publication must refuse immediately, not deadlock.
            with pytest.raises(ProviderError, match="replay_busy"):
                with s._hold_retained_public_input(token):
                    pytest.fail("overlapping replay guard")
            thread = Thread(target=reconnect)
            thread.start()
            assert started.wait(5)
            assert not closed.wait(.05)
            assert len(requests) == 1

        assert closed.wait(5)
        thread.join(timeout=5)
        with s._hold_retained_public_input(token) as held:
            assert held is None
        assert len(requests) == 1
    finally:
        if thread is not None:
            thread.join(timeout=5)
        s.close()


def test_commit_guard_refuses_expiration_and_releases_on_failure(monkeypatch):
    requests = fixture(monkeypatch)
    s, clock = session()
    try:
        _rows, token = s._search_with_retained_input("jefferson")
        with pytest.raises(RuntimeError, match="rollback"):
            with s._hold_retained_public_input(token) as held:
                assert held is not None
                raise RuntimeError("rollback")
        with s._hold_retained_public_input(token) as held:
            assert held is not None
        with s._public_cache._lock:
            with pytest.raises(ProviderError, match="replay_busy"):
                with s._hold_retained_public_input(token):
                    pytest.fail("cache lock bypass")
        # The failed cache admission must release the navigation lock too.
        clock[0] += TTL_SECONDS
        with s._hold_retained_public_input(token) as held:
            assert held is None
        assert len(requests) == 1
    finally:
        s.close()


def test_paginated_public_envelopes_retain_partial_without_second_get(monkeypatch):
    requests = fixture(monkeypatch)
    s, _ = session()
    try:
        response = s.search_with_response_facets("jefferson", scope="catalog")
        retained, token = s._search_response_with_retained_input("jefferson", "catalog")
        assert token is not None
        assert response.partial is True and retained.partial is True
        assert len(response.results) == 20
        assert retained.results == response.results == s.search("jefferson")
        assert len(response.public_notices) == len(response.results)
        assert len(retained.public_notices) == len(retained.results)
        assert len(requests) == 1
    finally:
        s.close()


def test_complete_public_page_does_not_claim_partial(monkeypatch):
    requests = fixture(monkeypatch)
    original = client.parse_public_search

    def complete_fixture(*args):
        # Controlled positive branch: a page with exactly its declared total
        # and no other pages. The original recorded fixture is paginated.
        page = original(*args)
        return replace(page, total_results=len(page.items), next_url=None,
                       pagination_links=(), complete=True)

    monkeypatch.setattr(client, "parse_public_search", complete_fixture)
    s, _ = session()
    try:
        result = s.search_with_response_facets("jefferson", scope="catalog")
        retained, token = s._search_response_with_retained_input("jefferson", "catalog")
        assert result.partial is False and retained.partial is False
        assert retained.results == result.results and token is not None
        assert len(requests) == 1
    finally:
        s.close()
