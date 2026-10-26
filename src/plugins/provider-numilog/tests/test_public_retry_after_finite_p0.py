"""P0 #1886: no infinite/astronomical Numilog public-catalogue 429 cooldown.

Entirely synthetic HTTP and monotonic clocks; no network, account cookies,
signed links or private product pages.
"""
from email.utils import formatdate
import math

import httpx
import pytest

from cifereca.plugin_api import ProviderError
import cifereca_numilog_public_client as client
from cifereca_numilog_public_cache import (
    MAX_RATE_LIMIT_PAUSE_SECONDS as CAP, PublicCatalogueCache,
    RATE_LIMIT_PAUSE_SECONDS as FLOOR,
)

PRODUCT = "https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook"


@pytest.mark.parametrize("value", [
    "9" * 128, "9999999999", str(CAP + 1),
    str(10**30), "000000" + str(CAP + 10000),
])
def test_large_valid_retry_after_seconds_always_finite_and_bounded(value):
    result = client._retry_after_seconds(value, now=1000)
    assert result == CAP and math.isfinite(result)


def test_extreme_http_date_does_not_create_forever_cooldown():
    later = formatdate(10_000_000_000, usegmt=True)
    assert client._retry_after_seconds(later, now=1000) == CAP
    assert client._retry_after_seconds(formatdate(1120, usegmt=True), now=1000) == 120


@pytest.mark.parametrize("source", ["search", "detail"])
@pytest.mark.parametrize("header", ["9"*100, str(10**30), "http-date"])
def test_429_header_caps_monotonic_deadline_and_recovers_after_cap(
        monkeypatch, source, header):
    monotonic = [0.0]
    cache = PublicCatalogueCache(clock=lambda: monotonic[0])
    if header == "http-date":
        header = formatdate(10_000_000_000, usegmt=True)
    monkeypatch.setattr(client.time, "time", lambda: 1000)
    requests = []
    original_client = client.HTTPX_TRANSPORT.client

    def respond(request):
        requests.append(request)
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(
            429, headers={"Retry-After": header},
            content=b'PRIVATE_429_RESPONSE',
        )

    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(respond),
    ))
    getter = (lambda: client.fetch_public_search("jefferson") if source == "search"
              else client.fetch_public_detail(PRODUCT))

    with pytest.raises(client.PublicRateLimitError) as exc:
        cache.get("first", getter)
    assert "PRIVATE_429_RESPONSE" not in str(exc.value)
    assert "PRIVATE_429_RESPONSE" not in repr(exc.value.__dict__)
    assert exc.value.retry_after_seconds == CAP
    assert math.isfinite(cache._paused_until)
    assert cache._paused_until == CAP
    cache.clear()  # Reconnect must neither evade nor make the pause permanent.
    monotonic[0] = CAP - 1
    with pytest.raises(ProviderError, match="rate_limited"):
        cache.get("second", lambda: pytest.fail("request during capped cooldown"))
    assert len(requests) == 1
    monotonic[0] = CAP
    assert cache.get("after", lambda: {"ok": True}) == {"ok": True}
    assert len(requests) == 1


@pytest.mark.parametrize("delay,expected", [
    (10**1000, CAP), (float("inf"), FLOOR),
    (float("-inf"), FLOOR), (float("nan"), FLOOR),
    (-1, FLOOR), (True, FLOOR), ("999999999999999", FLOOR),
    (0, FLOOR), (120, 120), (CAP+50, CAP),
])
def test_direct_untrusted_exception_retry_delay_never_poison_cache(delay, expected):
    monotonic = [0.0]
    cache = PublicCatalogueCache(clock=lambda: monotonic[0])
    def limited():
        err = ProviderError("Numilog : public_catalog_rate_limited")
        err.retry_after_seconds = delay
        raise err
    with pytest.raises(ProviderError, match="rate_limited"):
        cache.get("first", limited)
    assert cache._paused_until == expected
    assert math.isfinite(cache._paused_until)
    monotonic[0] = expected - 1
    with pytest.raises(ProviderError, match="rate_limited"):
        cache.get("still-blocked", lambda: pytest.fail("premature HTTP"))
    monotonic[0] = expected
    assert cache.get("recovered", lambda: {"public": True}) == {"public": True}


@pytest.mark.parametrize("header", [
    "PRIVATE ?token=PRIVATE", "\r\nRetry-After: 999",
    "nan", "inf", "1.5", "-1", "٩٩", None, "9"*129,
])
def test_unqualified_header_still_uses_minimum_safe_pause(header):
    assert client._retry_after_seconds(header, now=1000) == 0.0
