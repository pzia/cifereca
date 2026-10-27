"""Streaming searches honor the same public outage/backoff contract (#1916)."""
import httpx
import pytest

from cifereca.core import search_orchestration as orch
from tests.test_core_0968_provider_stream import Collection, PID, _library
from tests.test_core043_progress_cache_stats import _FakeSession
from tests.test_core_p1_1916_retry_after_cooldown import _http
from tests.test_core_p1_1916_stale_public_fallback import _known


def _failing_source(tmp_path, failure, when):
    collection = Collection([])
    service = _library(tmp_path, collection)
    session = collection.plugin.session

    def fail():
        session.stream_calls += 1
        raise failure()

    def iterator():
        fail()
        yield  # Failure while consuming the generator, not invoking it.

    session.search_stream = lambda *a, **kw: fail() if when == 'call' else iterator()
    service._sessions['good'] = _FakeSession('good')
    _known(service, provider=PID, identity=service.current_identity.identity_id,
           key='stream', title='Progressive Book')
    return service, session


@pytest.mark.parametrize('when', ['call', 'iteration'])
def test_stream_429_uses_stale_public_rows_without_repeating_until_retry_after(
    tmp_path, monkeypatch, when,
):
    clock = [1000.0]
    monkeypatch.setattr(orch, '_search_cooldown_clock', lambda: clock[0])
    service, session = _failing_source(tmp_path, lambda: _http(429, '60'), when)
    try:
        for _ in range(2):
            batches = []
            result = service.search_progressive(
                'Progressive Book', force=True, on_batch=batches.append)
            assert session.stream_calls == 1
            assert session.legacy_calls == 0
            assert result.providers_failed == 1 and result.result_count == 2
            failed = next(b for b in batches if b.provider_id == PID)
            assert failed.error_code == 'SearchTemporarilyUnavailable'
            assert failed.result_status == 'known_catalog_stale'
            assert failed.results[0].record_id == 'public-stream'
            assert not failed.results[0].downloadable and not failed.results[0].owned
            assert next(b for b in batches if b.provider_id == 'good').result_status == 'live'
        clock[0] += 61
        service.search_progressive('Progressive Book', force=True)
        assert session.stream_calls == 2
        assert session.legacy_calls == 0
    finally:
        service.close()


@pytest.mark.parametrize('failure,code,stale', [
    (lambda: _http(503), 'SearchTemporarilyUnavailable', True),
    (lambda: httpx.ReadTimeout('synthetic timeout'), 'SearchTemporarilyUnavailable', True),
    (lambda: _http(401), 'SearchAuthenticationRequired', False),
    (lambda: _http(403), 'SearchAccessDenied', False),
    (lambda: httpx.ProxyError('synthetic proxy failure'), 'SearchProxyUnavailable', False),
    (lambda: RuntimeError('synthetic stream failure'), 'StreamProviderFailed', False),
])
def test_stream_failures_preserve_refusal_semantics_and_do_not_resync_or_throttle(
    tmp_path, failure, code, stale,
):
    service, session = _failing_source(tmp_path, failure, 'iteration')
    try:
        for _ in range(2):
            batches = []
            result = service.search_progressive(
                'Progressive Book', force=True, on_batch=batches.append)
            failed = next(b for b in batches if b.provider_id == PID)
            assert failed.error_code == code
            assert bool(failed.results) is stale
            assert failed.result_status == ('known_catalog_stale' if stale else 'unavailable')
            assert result.providers_failed == 1
            assert next(b for b in batches if b.provider_id == 'good').results
        assert session.stream_calls == 2  # Only a native 429 supplies a cooldown.
        assert session.legacy_calls == 0  # No repair request to the same source.
        assert service.storage.catalog_entries(state='remote_owned') == []
    finally:
        service.close()
