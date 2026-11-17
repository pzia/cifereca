"""Memory-only cache/budget for ONE anonymous public catalogue session.

No account, cookies, owned rows, files or background retries. Closing or
reconnecting invalidates entries without waiting for an in-flight HTTP call.
"""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
import math
import threading
import time

from cifereca.plugin_api import ProviderError

TTL_SECONDS = 60.0
MAX_ENTRIES = 16
RATE_LIMIT_PAUSE_SECONDS = 60.0
# Never let an untrusted 429 response poison a session with an infinite or
# astronomically distant deadline. 30 days is an explicit conservative cap.
MAX_RATE_LIMIT_PAUSE_SECONDS = 30 * 24 * 60 * 60


class PublicCatalogueCache:
    def __init__(self, *, clock=None):
        self._clock = clock or time.monotonic
        self._entries = OrderedDict()
        self._lock = threading.Lock()
        self._epoch = 0
        self._inflight = None
        self._tokens = 2.0
        self._updated = self._clock()
        self._paused_until = 0.0

    def clear(self):
        with self._lock:
            self._epoch += 1
            self._entries.clear()
            # Budget/cooldown survive reconnect: reconnect is not a rate-limit
            # workaround. Keep the in-flight reservation until its own return.

    def _peek_retained_input(self, token):
        """Bounded cache-only lookup, no load callback, token or LRU renewal.

        The session owns the cache and must fence its own generation before
        and after replay. A retained input shares the existing entry's TTL,
        eviction and reconnect invalidation. Do not export raw query/body
        through the normal SearchResult or plugin UI surfaces.
        """
        with self._lock:
            return self._peek_retained_input_locked(token)

    def _peek_retained_input_locked(self, token):
        from cifereca_numilog_public_client import _RetainedSearchPage

        now = self._clock()
        for key, (until, _value) in tuple(self._entries.items()):
            if until <= now:
                self._entries.pop(key, None)
        matches = [(until, value) for until, value in self._entries.values()
                   if (type(value) is _RetainedSearchPage
                       and value.raw_input is not None
                       and value.raw_input.token == token)]
        if len(matches) != 1:
            return None, 0
        until, value = matches[0]
        return value, max(0, math.floor(until - now))

    @contextmanager
    def _hold_retained_input(self, token):
        """Publication guard; caller already owns its session generation lock.

        Nonblocking admission: no waiting for an ongoing cache operation.
        A successful guard pins the exact immutable input until exit; clear,
        eviction and replacement serialize behind it. No parsing/HTTP,
        SQLite access or TTL extension in this provider cache.
        """
        if not self._lock.acquire(blocking=False):
            raise ProviderError("Numilog : public_catalog_replay_busy")
        try:
            entry, ttl = self._peek_retained_input_locked(token)
            yield entry.raw_input if entry is not None and ttl >= 1 else None
        finally:
            self._lock.release()

    def get(self, key, load):
        with self._lock:
            now = self._clock()
            for expired in [k for k, (until, _v) in self._entries.items() if until <= now]:
                self._entries.pop(expired)
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return deepcopy(cached[1])
            token, epoch = self._reserve_locked(now)
        return self._run_reserved(key, token, epoch, load)

    def _reserve_locked(self, now):
        """Shared single in-flight slot, cooldown and token bucket."""
        if now < self._paused_until:
            raise ProviderError('Numilog : public_catalog_rate_limited')
        if self._inflight is not None:
            raise ProviderError('Numilog : public_catalog_request_in_progress')
        # Two-request burst allows search -> explicit detail. Thereafter
        # at most one new HTTP request per second, no sleeps or retries.
        self._tokens = min(2.0, self._tokens + max(0.0, now - self._updated))
        self._updated = now
        if self._tokens < 1.0:
            raise ProviderError('Numilog : public_catalog_request_budget_exhausted')
        self._tokens -= 1.0
        token, epoch = object(), self._epoch
        self._inflight = token
        return token, epoch

    def _reserve_retained_refresh_locked(self, input_token, record_id, transport_digest):
        """Caller holds cache lock under the session generation guard.

        An explicit refresh requires one still-live ordinary Search page
        retaining the exact original response, not a cache MISS or arbitrary
        URL. Reserve using exactly the normal HTTP budget.
        """
        entry, ttl = self._peek_retained_input_locked(input_token)
        if entry is None or ttl < 1:
            raise ProviderError("Numilog : public_catalog_refresh_input_unavailable")
        raw = entry.raw_input
        key = ("search", raw._url, "all")
        current = self._entries.get(key)
        if (current is None or current[1] is not entry
                or raw.transport_digest != transport_digest):
            raise ProviderError("Numilog : public_catalog_refresh_input_unavailable")
        matches = [row for row in entry.page.items if row.record_id == record_id]
        if len(matches) != 1:
            raise ProviderError("Numilog : public_catalog_refresh_input_unavailable")
        token, epoch = self._reserve_locked(self._clock())
        return token, epoch, key, raw, matches[0]

    def _release_reserved(self, token):
        with self._lock:
            if self._inflight is token:
                self._inflight = None

    def _run_reserved(self, key, token, epoch, load):
        try:
            value = load()  # No cache mutex held across HTTP.
            snapshot = deepcopy(value)
            with self._lock:
                if self._epoch != epoch:
                    # A close/reconnect invalidates the *response*, not just
                    # its cache entry. A late callback must not display rows
                    # from the session that initiated this HTTP request.
                    raise ProviderError('Numilog : public_catalog_request_invalidated')
                self._entries[key] = (self._clock() + TTL_SECONDS, snapshot)
                self._entries.move_to_end(key)
                while len(self._entries) > MAX_ENTRIES:
                    self._entries.popitem(last=False)
            return value
        except ProviderError as exc:
            if str(exc) in {'Numilog : public_catalog_rate_limited',
                            'Numilog : public_detail_rate_limited',
                            'Numilog : public_readzis_rate_limited'}:
                delay = getattr(exc, 'retry_after_seconds', 0.0)
                # The exception is a remote transport boundary too: a huge
                # integer can overflow float() and an infinite duration would
                # permanently poison this cache across reconnects.
                if type(delay) not in (int, float) or delay < 0:
                    bounded = 0.0
                else:
                    try:
                        numeric = float(delay)
                    except (OverflowError, ValueError):
                        numeric = float(MAX_RATE_LIMIT_PAUSE_SECONDS)
                    # Nonfinite *float* values are malformed headers/errors
                    # and keep the historical 60-second floor. Only an
                    # enormous but valid integer gets the finite upper cap.
                    bounded = (
                        min(numeric, MAX_RATE_LIMIT_PAUSE_SECONDS)
                        if math.isfinite(numeric) else 0.0
                    )
                with self._lock:
                    self._paused_until = max(
                        self._paused_until,
                        self._clock() + max(RATE_LIMIT_PAUSE_SECONDS, bounded),
                    )
            raise
        finally:
            self._release_reserved(token)
