"""#1916: HTTP 429 backoff is source-scoped, finite, and read-only.

Synthetic HTTP status exceptions only. No remote requests or GTK.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from cifereca.core import search_orchestration as orch
from cifereca.plugin_api import ProviderError
from cifereca.search_failures import search_retry_after_seconds
from tests.test_core043_progress_cache_stats import _FakeSession, _service
from tests.test_core_p1_1916_stale_public_fallback import _known


def _http(status, retry_after=None):
    request = httpx.Request("GET", "https://catalog.invalid/private?token=SECRET")
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    response = httpx.Response(status, request=request, headers=headers)
    return httpx.HTTPStatusError("PRIVATE_SIGNED_URL", request=request, response=response)


@pytest.mark.parametrize("value,expected", [
    ("120", 120), ("0", 30), ("999999999999999999999", 3600),
    ("nonsense SECRET", 30), ("0.5", 30), (None, 30),
])
def test_native_http_429_has_finite_retry_after_or_safe_default(value, expected):
    assert search_retry_after_seconds(_http(429, value), now=1000) == expected


def test_retry_after_rfc_date_is_bounded_and_expired_dates_use_default():
    future = format_datetime(datetime.fromtimestamp(1000, tz=timezone.utc) + timedelta(seconds=120))
    past = format_datetime(datetime.fromtimestamp(1000, tz=timezone.utc) - timedelta(days=1))
    assert search_retry_after_seconds(_http(429, future), now=1000) == 120
    assert search_retry_after_seconds(_http(429, past), now=1000) == 30


def test_only_native_429_and_complete_exception_graph_can_throttle():
    assert search_retry_after_seconds(_http(503, "600")) is None
    assert search_retry_after_seconds(httpx.ReadTimeout("PRIVATE")) is None
    fake = ProviderError("search_temporarily_unavailable: Retry-After: 600")
    fake.code = "search_temporarily_unavailable"
    assert search_retry_after_seconds(fake) is None

    mixed = ProviderError("PRIVATE_WRAPPER")
    mixed.__cause__ = _http(429, "120")
    mixed.__context__ = _http(403)
    assert search_retry_after_seconds(mixed) is None

    proxy = httpx.ProxyError("PRIVATE_PROXY")
    proxy.__cause__ = _http(429, "120")
    assert search_retry_after_seconds(proxy) is None

    cyclic = ProviderError("PRIVATE")
    cyclic.__cause__ = _http(429, "65")
    cyclic.__cause__.__cause__ = cyclic
    assert search_retry_after_seconds(cyclic, now=1000) == 65

    deep = _http(429, "65")
    cursor = deep
    for index in range(140):
        wrapped = ProviderError(f"PRIVATE_{index}")
        cursor.__context__ = wrapped
        cursor = wrapped
    assert search_retry_after_seconds(deep) is None


def test_progressive_429_cooldown_does_not_retry_and_isolates_good_source(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(orch, "_search_cooldown_clock", lambda: clock[0])
    calls, batches = [], []
    try:
        _known(service, provider="bad", key="old", title="Progressive Book")
        bad = _FakeSession("bad")
        good = _FakeSession("good")

        def rate_limited(query, scope, **kwargs):
            calls.append((query, scope))
            raise _http(429, "60")

        bad.search = rate_limited
        service._sessions = {"bad": bad, "good": good}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)

        for attempt in range(2):
            batches.clear()
            result = service.search_progressive(
                "Progressive Book", force=True, on_batch=batches.append,
            )
            assert len(calls) == 1
            assert result.providers_failed == 1
            assert result.result_count == 2
            stale = next(x for x in batches if x.provider_id == "bad")
            healthy = next(x for x in batches if x.provider_id == "good")
            assert stale.error_code == "SearchTemporarilyUnavailable"
            assert stale.result_status == "known_catalog_stale"
            assert healthy.result_status == "live"
            assert "PRIVATE_SIGNED" not in repr((batches, result))
        clock[0] += 61
        batches.clear()
        service.search_progressive(
            "Progressive Book", force=True, on_batch=batches.append,
        )
        assert len(calls) == 2  # cooled-down source becomes eligible again
    finally:
        service.close()


def test_direct_search_429_reuses_known_not_new_live_or_cached(tmp_path, monkeypatch):
    service = _service(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(orch, "_search_cooldown_clock", lambda: clock[0])
    calls = []
    try:
        _known(service, provider="bad", key="old", title="Progressive Book")
        bad = _FakeSession("bad")

        def rate_limited(*args, **kwargs):
            calls.append(1)
            raise _http(429, "120")

        bad.search = rate_limited
        service._sessions = {"bad": bad}
        for _ in range(2):
            rows = service.search("Progressive Book", force=True)
            assert len(rows) == 1
            assert rows[0].metadata["search_provenance"] == "known_catalog_stale"
        assert calls == [1]
        assert "recherche en ligne indisponible" in " ".join(service.last_search_warnings)
    finally:
        service.close()


def test_cooldown_cannot_cross_identity_credential_or_replaced_session(tmp_path, monkeypatch):
    service = _service(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(orch, "_search_cooldown_clock", lambda: clock[0])
    try:
        bad = _FakeSession("bad")
        identity = service.current_identity
        orch._record_source_retry_after(service, bad, identity, bad.credential_id, _http(429, "60"))
        assert orch._source_in_retry_after(service, bad, identity)
        assert not orch._source_in_retry_after(service, _FakeSession("bad"), identity)
        original = bad.credential_id
        bad.credential_id = "different-credential"
        assert not orch._source_in_retry_after(service, bad, identity)
        bad.credential_id = original
        assert orch._source_in_retry_after(service, bad, identity)
        other = object()
        assert not orch._source_in_retry_after(service, bad, other)
        clock[0] += 61
        assert not orch._source_in_retry_after(service, bad, identity)
    finally:
        service.close()
