"""#1916: proxy errors must neither reuse stale public rows nor retry variants."""
from __future__ import annotations

import httpx
import pytest

from cifereca.core.known_catalog_fallback import public_service_unavailable
from cifereca.plugin_api import ProviderError
from cifereca.search_failures import search_failure_code, should_stop_search_variants
from tests.test_core043_progress_cache_stats import _FakeSession, _service
from tests.test_core_p1_1916_stale_public_fallback import _known


def _proxy_response_error():
    request = httpx.Request("GET", "https://catalog.invalid/?secret=PRIVATE")
    return httpx.HTTPStatusError(
        "PRIVATE_PROXY_AUTH", request=request,
        response=httpx.Response(407, request=request),
    )


def _wrapped(error: BaseException, *, generic_outage: bool):
    wrapper = ProviderError("PRIVATE_ACCOUNT")
    if generic_outage:
        wrapper.code = "search_temporarily_unavailable"
    wrapper.__cause__ = error
    return wrapper


@pytest.mark.parametrize("error_factory", [
    lambda: httpx.ProxyError("PRIVATE_PROXY_URL"),
    _proxy_response_error,
])
@pytest.mark.parametrize("generic_outage", [False, True])
def test_proxy_is_not_a_public_catalogue_outage_even_under_generic_wrapper(
    error_factory, generic_outage,
):
    error = _wrapped(error_factory(), generic_outage=generic_outage)
    assert search_failure_code(error) == "SearchProxyUnavailable"
    assert should_stop_search_variants(error)
    assert not public_service_unavailable(error)


@pytest.mark.parametrize("error_factory", [
    lambda: httpx.ProxyError("PRIVATE_PROXY_LOGIN"),
    _proxy_response_error,
])
def test_proxy_in_forked_exception_graph_outranks_transport_outage(error_factory):
    outer = httpx.ReadTimeout("PRIVATE_SIGNED_CATALOG_URL")
    outer.__cause__ = error_factory()
    outer.__context__ = ProviderError("PRIVATE_OUTER")
    outer.__context__.code = "search_temporarily_unavailable"
    outer.__suppress_context__ = True
    assert search_failure_code(outer) == "SearchProxyUnavailable"
    assert not public_service_unavailable(outer)

    # A genuine provider refusal still has precedence, across both branches.
    request = httpx.Request("GET", "https://catalog.invalid/secret")
    denied = httpx.HTTPStatusError(
        "PRIVATE_PROVIDER", request=request,
        response=httpx.Response(403, request=request),
    )
    outer.__context__.__cause__ = denied
    assert search_failure_code(outer) == "SearchAccessDenied"
    assert not public_service_unavailable(outer)


@pytest.mark.parametrize("error_factory", [
    lambda: httpx.ProxyError("PRIVATE_PROXY"),
    _proxy_response_error,
])
def test_proxy_fault_stops_federated_variants_without_promoting_known_stale(
    tmp_path, monkeypatch, error_factory,
):
    service = _service(tmp_path)
    attempts, batches = [], []
    try:
        _known(service, provider="bad", title="Maritime Book")
        unavailable = _FakeSession("bad")

        def search(*args, **kwargs):
            attempts.append(args)
            raise _wrapped(error_factory(), generic_outage=True)

        unavailable.search = search
        service._sessions = {"bad": unavailable, "good": _FakeSession("good")}
        monkeypatch.setattr(
            service, "_search_query_variants",
            lambda _q: ["original", "same-proxy-variant", "would-repeat"],
        )
        summary = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        failed = next(batch for batch in batches if batch.provider_id == "bad")
        assert len(attempts) == 1
        assert failed.error_code == "SearchProxyUnavailable"
        assert failed.results == ()
        assert summary.providers_failed == 1
        assert any(b.provider_id == "good" and b.results for b in batches)
        assert "PRIVATE_" not in repr((summary, batches))
    finally:
        service.close()
