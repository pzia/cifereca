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
    httpx.DecodingError("PRIVATE_TOKEN malformed content-encoding"),
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


class _UntrustedSearchError(Exception):
    @property
    def code(self):
        raise RuntimeError("https://private.invalid/token?secret=DO_NOT_ECHO")


class _UntrustedValue:
    def __eq__(self, _other):
        raise AssertionError("never compare an untrusted code")


def test_untrusted_error_properties_never_authorize_stale_fallback():
    private = _UntrustedSearchError("PRIVATE_ACCOUNT")
    assert search_failure_code(private) is None
    assert not is_technical_search_error(private)
    assert not public_service_unavailable(private)

    foreign = ProviderError("PRIVATE_ACCOUNT")
    foreign.code = _UntrustedValue()
    assert search_failure_code(foreign) is None
    assert not public_service_unavailable(foreign)

    # Known transport and access-refusal evidence in a wrapped cause is still
    # authoritative, independently of a corrupt outer plugin property.
    private.__cause__ = httpx.ReadTimeout("PRIVATE")
    assert search_failure_code(private) == "SearchTemporarilyUnavailable"
    assert public_service_unavailable(private)

    private.__cause__ = _http_error(403)
    assert search_failure_code(private) == "SearchAccessDenied"
    assert not public_service_unavailable(private)


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


@pytest.mark.parametrize(("provider_code", "expected"), [
    ("search_authentication_required", "SearchAuthenticationRequired"),
    ("search_access_denied", "SearchAccessDenied"),
    ("search_temporarily_unavailable", "SearchTemporarilyUnavailable"),
    ("search_does_not_exist", None),
])
def test_closed_public_provider_machine_codes_are_exact(
    provider_code, expected,
):
    error = ProviderError("PRIVATE_PROVIDER_ACCOUNT")
    error.code = provider_code
    assert search_failure_code(error) == expected
    assert public_service_unavailable(error) == (
        expected == "SearchTemporarilyUnavailable"
    )


def test_explicit_provider_refusal_outweighs_generic_outage_wrapper():
    access = ProviderError("PRIVATE")
    access.code = "search_access_denied"
    access.__cause__ = httpx.ReadTimeout("PRIVATE")
    assert search_failure_code(access) == "SearchAccessDenied"
    assert not public_service_unavailable(access)

    # A genuine HTTP refusal in the chain outranks an inaccurate provider
    # wrapper and must never authorize stale results.
    outage = _wrapped(_http_error(401), outage=True)
    assert search_failure_code(outage) == "SearchAuthenticationRequired"
    assert not public_service_unavailable(outage)


def test_redirect_loop_stops_query_variants_but_never_authorizes_stale():
    from cifereca.search_failures import should_stop_search_variants

    error = httpx.TooManyRedirects("PRIVATE_SSO_REDIRECT_SIGNED")
    wrapped = _wrapped(error)
    assert search_failure_code(error) is None
    assert search_failure_code(wrapped) is None
    assert not is_technical_search_error(wrapped)
    assert should_stop_search_variants(wrapped)
    assert not public_service_unavailable(wrapped)

    # A cyclic chain cannot trap the traversal, and a normal semantic
    # rejection remains eligible for established variant handling.
    wrapped.__cause__.__cause__ = wrapped
    assert should_stop_search_variants(wrapped)
    assert not should_stop_search_variants(_wrapped(_http_error(404)))


def test_sso_redirect_loop_does_not_retry_or_return_known_public_catalogue(
    tmp_path, monkeypatch,
):
    from cifereca.search_failures import should_stop_search_variants

    service = _service(tmp_path)
    attempts, batches = [], []
    try:
        _known(service, provider="bad", title="Maritime Book")
        bad = _FakeSession("bad")
        def loop(*args, **kwargs):
            attempts.append(args)
            raise _wrapped(httpx.TooManyRedirects(
                "PRIVATE_PERSONAL_LOGIN_REDIRECT?token=SIGNED"
            ))
        bad.search = loop
        service._sessions = {"bad": bad, "good": _FakeSession("good")}
        monkeypatch.setattr(
            service, "_search_query_variants",
            lambda _q: ["original", "would-repeat-same-route", "again"],
        )
        result = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        failed = next(batch for batch in batches if batch.provider_id == "bad")
        assert len(attempts) == 1
        assert failed.error_code == "ProviderSearchFailed"
        assert failed.results == ()  # No stale results for an auth loop.
        assert any(batch.provider_id == "good" and batch.results for batch in batches)
        assert result.providers_failed == 1
        assert "PRIVATE_PERSONAL" not in repr((result, batches))
        assert not public_service_unavailable(
            httpx.TooManyRedirects("PRIVATE_PERSONAL_LOGIN_REDIRECT")
        )
    finally:
        service.close()


def _forked_exception(explicit, implicit):
    """Build an exception with BOTH branches, including suppressed context."""
    wrapped = ProviderError("PRIVATE_ACCOUNT_OR_BOOK_TITLE")
    wrapped.__cause__ = explicit
    wrapped.__context__ = implicit
    wrapped.__suppress_context__ = True
    return wrapped


@pytest.mark.parametrize("status,expected", [
    (401, "SearchAuthenticationRequired"), (403, "SearchAccessDenied"),
])
@pytest.mark.parametrize("refusal_branch", ["context", "cause"])
def test_forked_cause_and_context_never_mask_a_http_refusal(
    status, expected, refusal_branch,
):
    timeout = httpx.ReadTimeout("PRIVATE_SIGNED_URL")
    refusal = _http_error(status)
    error = _forked_exception(
        refusal if refusal_branch == "cause" else timeout,
        refusal if refusal_branch == "context" else timeout,
    )
    assert search_failure_code(error) == expected
    assert is_technical_search_error(error)
    assert not public_service_unavailable(error)
    error.__context__.__cause__ = error  # arbitrary cycle
    assert search_failure_code(error) == expected


def test_forked_redirect_context_stops_variants_without_authorizing_stale():
    from cifereca.search_failures import should_stop_search_variants
    error = _forked_exception(
        ProviderError("PRIVATE_OTHER_REASON"),
        httpx.TooManyRedirects("PRIVATE_LOGIN_REDIRECT"),
    )
    assert search_failure_code(error) is None
    assert should_stop_search_variants(error)
    assert not public_service_unavailable(error)


def test_forked_http_refusal_blocks_stale_in_real_federation(tmp_path):
    service = _service(tmp_path)
    calls, batches = [], []
    try:
        _known(service, provider="bad", title="Maritime Book")
        bad = _FakeSession("bad")
        def search(*args, **kwargs):
            calls.append(args)
            raise _forked_exception(
                httpx.ReadTimeout("PRIVATE_SIGNED_ACSM_URL"),
                _http_error(403),
            )
        bad.search = search
        service._sessions = {"bad": bad, "good": _FakeSession("good")}
        summary = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        result = next(row for row in batches if row.provider_id == "bad")
        assert len(calls) == 1
        assert result.error_code == "SearchAccessDenied"
        assert result.results == ()
        assert summary.providers_failed == 1
        assert any(row.provider_id == "good" and row.results for row in batches)
        assert "PRIVATE_" not in repr((batches, summary))
    finally:
        service.close()


def test_exception_graph_budget_fails_closed_without_stale_permission():
    from cifereca.search_failures import should_stop_search_variants
    root = httpx.ReadTimeout("PRIVATE_OUTAGE")
    current = root
    for i in range(150):
        wrapper = ProviderError("PRIVATE_" + str(i))
        current.__context__ = wrapper
        current = wrapper
    assert search_failure_code(root) is None
    assert not public_service_unavailable(root)
    assert should_stop_search_variants(root)
