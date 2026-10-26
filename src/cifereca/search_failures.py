"""A looser query cannot repair transport, authentication or malformed XML."""
import httpx
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import time
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



# Per-service, per-session cooldowns are deliberately bounded. These values
# are only consumed when native HTTP 429 evidence exists (not provider text).
_PUBLIC_RETRY_MIN_SECONDS = 30
_PUBLIC_RETRY_MAX_SECONDS = 3600


def _parsed_public_retry_after(raw: object, now: float) -> int:
    """Parse HTTP Retry-After numeric/date without retaining the header text."""
    if type(raw) is not str or not raw or len(raw) > 80 or not raw.isascii():
        return 0
    if raw.isdigit():
        # Treat huge integers as the finite cap; never parse unbounded digits.
        if len(raw) > 12:
            return _PUBLIC_RETRY_MAX_SECONDS
        return min(int(raw), _PUBLIC_RETRY_MAX_SECONDS)
    try:
        at = parsedate_to_datetime(raw)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        delay = at.timestamp() - now
        if delay <= 0:
            return 0
        return _PUBLIC_RETRY_MAX_SECONDS if delay > _PUBLIC_RETRY_MAX_SECONDS else int(delay) + (delay % 1 > 0)
    except (ValueError, TypeError, OverflowError, IndexError):
        return 0


def search_retry_after_seconds(error: BaseException, *, now: float | None = None) -> int | None:
    """Return a finite 429 cooldown, or None without trustworthy HTTP evidence.

    Native httpx HTTPStatusError 429 only: never trust arbitrary provider
    messages, URLs or dynamic exception properties. Access/refusal/proxy
    conflicts and truncated exception graphs fail closed.
    """
    if search_failure_code(error) != "SearchTemporarilyUnavailable":
        return None
    now = time() if now is None else now
    seen: set[int] = set()
    pending = [error]
    found_429 = False
    delay = 0
    while pending and len(seen) < 128:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            response = current.response
            if response.status_code == 429:
                found_429 = True
                try:
                    raw = response.headers.get("retry-after")
                except (ValueError, TypeError):
                    raw = None
                delay = max(delay, _parsed_public_retry_after(raw, now))
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    if pending or not found_429:
        return None
    return min(_PUBLIC_RETRY_MAX_SECONDS, max(_PUBLIC_RETRY_MIN_SECONDS, delay))



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
