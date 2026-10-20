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
    unavailable = False
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status == 401:
                return "SearchAuthenticationRequired"
            if status == 403:
                return "SearchAccessDenied"
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
        if ((type(provider_code) is str
             and provider_code == "search_temporarily_unavailable")
                or isinstance(error, (httpx.TransportError, ParseError))):
            unavailable = True
        error = error.__cause__ or error.__context__
    return "SearchTemporarilyUnavailable" if unavailable else None


def is_technical_search_error(error):
    return search_failure_code(error) is not None
