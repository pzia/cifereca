"""#7163: public Numilog 503 is retryable but never an exhaustive zero.

Synthetic anonymous HTTP only; no account client, supplier trace or real GET.
"""
import pytest

from cifereca.plugin_api import ProviderError
import cifereca_numilog_public_client as client
from cifereca_numilog_public_cache import PublicCatalogueCache
from tests.test_numilog_public_http_p0 import (
    FakeResponse, setup_public_client, _overlapping_readers,
)


def test_public_search_503_respects_retry_after_across_cache_reconnect(monkeypatch):
    response = FakeResponse(status=503)
    response.headers["retry-after"] = "120"
    fake, _ = setup_public_client(monkeypatch, response)
    cache = PublicCatalogueCache(clock=lambda: 500.0, wall_clock=lambda: 1000.0)
    key = ("search", client.public_search_url("jefferson"), "all")

    with pytest.raises(client.PublicServiceUnavailableError) as failed:
        cache.get(key, lambda: client.fetch_public_search("jefferson"))
    assert str(failed.value) == "Numilog : public_catalog_unavailable"
    assert failed.value.retry_after_seconds == 120
    assert cache.purchase_retry_delay() == 120
    assert fake.requests == [("GET", client.public_search_url("jefferson"))]

    # The cooldown is not an implicit retry and cannot be bypassed by a
    # reconnect or a second public query on the same anonymous session.
    cache.clear()
    with pytest.raises(ProviderError, match="public_catalog_rate_limited"):
        cache.get(("search", client.public_search_url("different"), "all"),
                  lambda: client.fetch_public_search("different"))
    assert len(fake.requests) == 1


def test_public_search_503_exact_waiters_keep_same_error_and_retry_window(monkeypatch):
    response = FakeResponse(status=503)
    response.headers["retry-after"] = "180"
    read = lambda: client.fetch_public_search("jefferson")
    fake, first, second = _overlapping_readers(
        monkeypatch, response, read, read,
    )
    for result in (first, second):
        with pytest.raises(client.PublicServiceUnavailableError) as failed:
            result.result()
        assert str(failed.value) == "Numilog : public_catalog_unavailable"
        assert failed.value.retry_after_seconds == 180
    assert fake.requests == [("GET", client.public_search_url("jefferson"))]
    assert not client._PUBLIC_SEARCH_FLIGHTS


@pytest.mark.parametrize("header,expected_pause", [
    ("999999999999999999999999", 30 * 24 * 60 * 60),
    ("not-a-date", 60),
])
def test_public_search_503_never_creates_unbounded_pause(monkeypatch, header, expected_pause):
    response = FakeResponse(status=503)
    response.headers["retry-after"] = header
    fake, _ = setup_public_client(monkeypatch, response)
    cache = PublicCatalogueCache(clock=lambda: 500.0)
    with pytest.raises(client.PublicServiceUnavailableError):
        cache.get(("search", client.public_search_url("jefferson"), "all"),
                  lambda: client.fetch_public_search("jefferson"))
    assert cache.purchase_retry_delay() == expected_pause
    assert len(fake.requests) == 1
