"""A looser query cannot repair transport, authentication or malformed XML."""
import httpx
from xml.etree.ElementTree import ParseError


def search_failure_code(error: BaseException) -> str | None:
    """Classify wrapped failures without exporting provider exception text.

    An explicit HTTP access refusal anywhere in the cause chain takes priority
    over an outer generic outage marker. Such a refusal must never authorize
    a stale public-catalogue fallback. Cyclic exception chains are tolerated.
    """
    seen = set()
    pending = [error]
    unavailable = False
    proxy_unavailable = False
    provider_refusal = None
    # Explicit and implicit exception links can both exist, even when
    # context display is suppressed. A 401/403 on either branch forbids
    # reusing stale public catalogue data.
    while pending and len(seen) < 128:
        error = pending.pop()
        if error is None or id(error) in seen:
            continue
        seen.add(id(error))
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status == 401:
                return "SearchAuthenticationRequired"
            if status == 403:
                return "SearchAccessDenied"
            # A 407 is an intermediary authentication challenge, not proof
            # that the public provider is offline. Never publish its stale
            # catalogue or repeat search variants through the same proxy.
            if status == 407:
                proxy_unavailable = True
            # HTTP 408 is a server-side request timeout; unlike 400/404,
            # it does not establish a legitimate zero-result response.
            unavailable |= status in {408, 429} or status >= 500
        # Provider exception properties are untrusted. A descriptor may
        # raise, or its equality operator may execute arbitrary plugin code.
        # Only a native, closed machine code may authorize public fallback.
        try:
            provider_code = getattr(error, "code", None)
        except Exception:
            provider_code = None
        if type(provider_code) is str:
            if provider_code == "search_authentication_required":
                provider_refusal = provider_refusal or "SearchAuthenticationRequired"
            elif provider_code == "search_access_denied":
                provider_refusal = provider_refusal or "SearchAccessDenied"
            elif provider_code == "search_temporarily_unavailable":
                unavailable = True
        # An invalid Content-Encoding body is a protocol/data-read failure:
        # httpx.DecodingError derives from RequestError, not TransportError.
        # Distinguish it from redirect loops, which may mean login/auth.
        if isinstance(error, httpx.ProxyError):
            # httpx.ProxyError is a TransportError, but an intermediary error
            # can hide a proxy-login refusal. It does not attest a public
            # catalogue outage, even under a generic provider wrapper.
            proxy_unavailable = True
        elif isinstance(error, (httpx.TransportError, httpx.DecodingError, ParseError)):
            unavailable = True
        if error.__context__ is not None:
            pending.append(error.__context__)
        if error.__cause__ is not None:
            pending.append(error.__cause__)
    # An exhausted graph may hide an HTTP 401/403. A partial inspection
    # cannot authorize known-catalog stale fallback as if no refusal existed.
    if pending:
        return provider_refusal or ("SearchProxyUnavailable" if proxy_unavailable else None)
    # A closed, built-in provider refusal outranks a generic timeout marker,
    # but a concrete underlying HTTP 401/403 outranks both.
    return (provider_refusal or ("SearchProxyUnavailable" if proxy_unavailable else None)
            or ("SearchTemporarilyUnavailable" if unavailable else None))


def is_technical_search_error(error):
    return search_failure_code(error) is not None


def should_stop_search_variants(error: BaseException) -> bool:
    """Do not retry query variants after a dead route or authentication loop.

    A redirect loop may reflect SSO/authentication, not a public service
    outage. It stops repeated requests to the same path but MUST NOT enable
    a stale-catalogue fallback. Never inspect exception messages or URLs.
    """
    if is_technical_search_error(error):
        return True
    seen = set()
    pending = [error]
    while pending and len(seen) < 128:
        error = pending.pop()
        if error is None or id(error) in seen:
            continue
        seen.add(id(error))
        if isinstance(error, httpx.TooManyRedirects):
            return True
        if error.__context__ is not None:
            pending.append(error.__context__)
        if error.__cause__ is not None:
            pending.append(error.__cause__)
    # Unsupported depth is not permission to retry the same unknown route.
    return bool(pending)
