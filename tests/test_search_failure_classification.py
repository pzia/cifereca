"""Public search failures preserve access refusals through provider wrappers."""
import httpx
import pytest
from xml.etree.ElementTree import ParseError

from cifereca.core.known_catalog_fallback import public_service_unavailable
from cifereca.plugin_api import ProviderError
from cifereca.search_failures import is_technical_search_error, search_failure_code
from tests.test_core043_progress_cache_stats import _FakeSession, _service
from tests.test_core_p1_1916_stale_public_fallback import _known


def _http_error(status):
    request = httpx.Request("GET", "https://catalog.invalid/?token=PRIVATE")
    return httpx.HTTPStatusError(
        "PRIVATE", request=request, response=httpx.Response(status, request=request),
    )


def _wrapped(error, *, outage=False):
    wrapper = ProviderError("PRIVATE")
    if outage:
        wrapper.code = "search_temporarily_unavailable"
    wrapper.__cause__ = error
    return wrapper


@pytest.mark.parametrize("status", [401, 403])
def test_access_refusal_overrides_generic_outage_wrapper(status):
    assert not public_service_unavailable(_wrapped(_http_error(status), outage=True))


@pytest.mark.parametrize("error", [
    httpx.ConnectError("PRIVATE"), httpx.ReadTimeout("PRIVATE"),
    _http_error(429), _http_error(503), ParseError("PRIVATE"),
])
def test_wrapped_outages_keep_the_same_no_retry_and_offline_fallback_policy(error):
    wrapped = _wrapped(_wrapped(error))
    assert public_service_unavailable(wrapped)
    assert is_technical_search_error(wrapped)
    assert search_failure_code(wrapped) == "SearchTemporarilyUnavailable"


def test_access_refusal_in_implicit_context_overrides_transport_error():
    error = httpx.ReadTimeout("PRIVATE")
    error.__context__ = _http_error(403)
    assert search_failure_code(error) == "SearchAccessDenied"
    assert is_technical_search_error(error)
    assert not public_service_unavailable(error)


@pytest.mark.parametrize("outage", [False, True])
def test_cyclic_wrappers_terminate_without_inventing_an_outage(outage):
    outer = _wrapped(ProviderError("PRIVATE"), outage=outage)
    outer.__cause__.__cause__ = outer
    assert public_service_unavailable(outer) is outage
    assert is_technical_search_error(outer) is outage


@pytest.mark.parametrize("status", [400, 404, 422])
def test_semantic_http_failure_does_not_authorize_stale_catalogue(status):
    error = _wrapped(_http_error(status))
    assert search_failure_code(error) is None
    assert not is_technical_search_error(error)
    assert not public_service_unavailable(error)


@pytest.mark.parametrize("status,expected", [
    (401, "SearchAuthenticationRequired"), (403, "SearchAccessDenied"),
])
@pytest.mark.parametrize("outage_wrapper", [False, True])
def test_provider_wrapped_refusals_never_return_stale_or_retry(
    tmp_path, status, expected, outage_wrapper,
):
    service = _service(tmp_path)
    calls, batches = [], []
    try:
        _known(service, provider="bad", title="Maritime Book")
        session = _FakeSession("bad")
        def search(*args, **kwargs):
            calls.append(args)
            raise _wrapped(_http_error(status), outage=outage_wrapper)
        session.search = search
        service._sessions = {"bad": session, "good": _FakeSession("good")}
        summary = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        failed = next(b for b in batches if b.provider_id == "bad")
        assert len(calls) == 1
        assert failed.error_code == expected
        assert failed.results == ()
        assert summary.providers_failed == 1
        assert any(b.provider_id == "good" and b.results for b in batches)
        assert "PRIVATE" not in str(batches) + str(summary)
    finally:
        service.close()
