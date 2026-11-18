"""#3724 exact public refresh uses the existing anonymous transport and budget."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from threading import Event, Thread
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderError
import cifereca_numilog_public_client as client
import cifereca_numilog_public_provider as provider
from cifereca_numilog_public_cache import PublicCatalogueCache

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




def control():
    return SimpleNamespace(check_cancelled=lambda: None)


def prepare(monkeypatch):
    requests = fixture(monkeypatch)
    s, clock = session()
    rows, token = s._search_with_retained_input("jefferson")
    raw, _ttl = s._read_retained_public_input(token)
    policy = [True]

    @contextmanager
    def hold():
        yield policy[0]

    def reserve(**changes):
        fields = dict(input_token=token, record_id=rows[0].record_id,
                      transport_digest=raw.transport_digest, hold_start_authority=hold)
        return s._reserve_exact_public_refresh(**(fields | changes))

    return s, clock, requests, rows, token, policy, reserve


def test_explicit_same_page_refresh_uses_one_shared_slot_and_returns_exact_row(monkeypatch):
    s, clock, requests, rows, token, policy, reserve = prepare(monkeypatch)
    try:
        with pytest.raises(ProviderError, match="refresh_input_unavailable"):
            reserve(record_id="foreign")
        with pytest.raises(ProviderError, match="refresh_input_unavailable"):
            reserve(transport_digest="0"*64)
        reservation = reserve()
        assert len(requests) == 1
        assert s.search("jefferson") == list(rows)  # ordinary HIT stays available
        with pytest.raises(ProviderError, match="request_in_progress"):
            s.search("other query")
        response, observed_at = reservation.execute(control())
        assert observed_at > 0
        assert response.results == [rows[0]]
        assert response.public_notices[0].result is response.results[0]
        assert len(requests) == 2 and requests[0] == requests[1]
        assert s._read_retained_public_input(token) == (None, 0)
        reservation.release()
        with pytest.raises(ProviderError, match="refresh_consumed"):
            reservation.execute(control())
        with pytest.raises(ProviderError, match="request_budget_exhausted"):
            s.search("other query")
        assert len(requests) == 2
    finally:
        s.close()


@pytest.mark.parametrize("mode", ("policy", "close", "expired", "release", "client_revoke"))
def test_revoke_before_transport_start_means_zero_additional_get(monkeypatch, mode):
    s, clock, requests, rows, token, policy, reserve = prepare(monkeypatch)
    try:
        reservation = reserve()
        if mode == "policy":
            policy[0] = False
        elif mode == "close":
            s.close()
            s.connect()
        elif mode == "expired":
            clock[0] += 61
        elif mode == "release":
            reservation.release()
        else:
            original = client.HTTPX_TRANSPORT.client

            def construct(**kwargs):
                policy[0] = False
                return original(**kwargs)

            monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", construct)
        with pytest.raises(ProviderError, match="refresh_(revoked|consumed)"):
            reservation.execute(control())
        reservation.release()
        assert len(requests) == 1 and s._public_cache._inflight is None
    finally:
        s.close()


@pytest.mark.parametrize("mode", ("changed_edition", "reconnect_during_parse"))
def test_late_response_or_changed_coordinates_never_return_notice(monkeypatch, mode):
    s, clock, requests, rows, token, policy, reserve = prepare(monkeypatch)
    try:
        original = client.parse_public_search

        def changed(*args):
            page = original(*args)
            if mode == "reconnect_during_parse":
                s.close()
                s.connect()
                return page
            return replace(page, items=(replace(page.items[0], docbase="OTHER"), *page.items[1:]))

        monkeypatch.setattr(client, "parse_public_search", changed)
        reservation = reserve()
        with pytest.raises(ProviderError, match="(request_invalidated|refresh_coordinate_changed)"):
            reservation.execute(control())
        assert len(requests) == 2 and requests[0] == requests[1]
        assert s._public_cache._inflight is None
        with pytest.raises(ProviderError, match="refresh_consumed"):
            reservation.execute(control())
        if mode == "changed_edition":
            assert s.search("jefferson") == list(rows)
        assert len(requests) == 2
    finally:
        s.close()


def test_refresh_429_uses_existing_cooldown_and_keeps_previous_page(monkeypatch):
    s, clock, requests, rows, token, policy, reserve = prepare(monkeypatch)
    try:
        blocked = fixture(monkeypatch, status=429)
        reservation = reserve()
        with pytest.raises(ProviderError, match="rate_limited"):
            reservation.execute(control())
        assert len(blocked) == 1
        assert s.search("jefferson") == list(rows)
        with pytest.raises(ProviderError, match="rate_limited"):
            reserve()
        assert len(blocked) == 1
        assert s._public_cache._inflight is None
    finally:
        s.close()


def test_concurrent_close_during_admitted_get_drops_response_and_keeps_slot_until_exit(monkeypatch):
    s, clock, requests, rows, token, policy, reserve = prepare(monkeypatch)
    entered, finish, closed = Event(), Event(), Event()
    errors = []
    worker = None
    closer = None
    try:
        original = client.parse_public_search
        def parsing(*args):
            entered.set()
            assert finish.wait(5)
            return original(*args)

        monkeypatch.setattr(client, "parse_public_search", parsing)
        reservation = reserve()

        def run():
            try:
                reservation.execute(control())
            except Exception as exc:
                errors.append(exc)

        def close_and_reopen():
            s.close()
            s.connect()
            closed.set()

        worker = Thread(target=run)
        worker.start()
        assert entered.wait(5) and len(requests) == 2
        # Release during execution cannot free the shared HTTP slot.
        reservation.release()
        closer = Thread(target=close_and_reopen)
        closer.start()
        assert closed.wait(5)
        with pytest.raises(ProviderError, match="request_in_progress"):
            s.search("other query")
        finish.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert len(errors) == 1 and "request_invalidated" in str(errors[0])
        assert s._public_cache._inflight is None
        assert s._read_retained_public_input(token) == (None, 0)
        assert len(requests) == 2
    finally:
        finish.set()
        if worker is not None:
            worker.join(timeout=5)
        if closer is not None:
            closer.join(timeout=5)
        s.close()
