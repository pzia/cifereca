"""Memory-only cache/budget for ONE anonymous public catalogue session.

No account, cookies, owned rows, files or background retries. Closing or
reconnecting invalidates entries without waiting for an in-flight HTTP call.
"""
from collections import OrderedDict
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

    def get(self, key, load):
        with self._lock:
            now = self._clock()
            for expired in [k for k, (until, _v) in self._entries.items() if until <= now]:
                self._entries.pop(expired)
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return deepcopy(cached[1])
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
            with self._lock:
                if self._inflight is token:
                    self._inflight = None
