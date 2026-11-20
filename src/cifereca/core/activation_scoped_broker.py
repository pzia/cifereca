"""Activation-scoped optional broker leases for read-only plugin ports.

The manager owns a resolver per activation. Reusing an activation keeps its
resolver; retiring it immediately invalidates every scoped operation. Existing
brokers which do not opt in retain the former resolution protocol.
"""
from threading import Event, Lock


class ActivationBrokerResolver:
    def __init__(self, resolve, lookup, plugin_id, declared):
        self._resolve = resolve
        self._lookup = lookup
        self._plugin_id = plugin_id
        self._declared = frozenset(declared)
        self._closed = Event()
        self._lock = Lock()
        self._scoped = {}

    def current(self):
        return not self._closed.is_set()

    def __call__(self, broker_id):
        broker = self._lookup(broker_id)
        factory = getattr(broker, "for_activation", None)
        if not callable(factory):
            # Compatibility: do not alter the lifetime of legacy brokers.
            return self._resolve(broker_id)
        if type(broker_id) is not str or broker_id not in self._declared:
            return None
        with self._lock:
            if not self.current():
                return None
            if broker_id not in self._scoped:
                self._scoped[broker_id] = factory(self._plugin_id, current=self.current)
            return self._scoped[broker_id]

    def close(self):
        # No I/O, no waiting for a reader that may be hashing user bytes.
        # A reader already admitted can finish, but cannot publish evidence.
        self._closed.set()


class ActivationScopedReadFactory:
    """Core registers a factory, not a shared stateful reader.

    Explicit method names only; no writer or private recovery handle can
    cross this adapter. Every instance is exclusive to one plugin activation.
    """
    def __init__(self, create, methods, *, refusals=None):
        self._create = create
        self._methods = frozenset(methods)
        self._refusals = dict(refusals or {})

    def for_activation(self, plugin_id, *, current):
        if type(plugin_id) is not str or not plugin_id or current() is not True:
            return None
        return _ActivationReadLease(self._create(), self._methods, self._refusals, current)


class _ActivationReadLease:
    __slots__ = ("_reader", "_methods", "_refusals", "_current")

    def __init__(self, reader, methods, refusals, current):
        self._reader, self._methods = reader, methods
        self._refusals, self._current = refusals, current

    def __getattr__(self, name):
        if name not in self._methods:
            raise AttributeError(name)
        method = getattr(self._reader, name)

        def guarded(*args, **kwargs):
            fallback = self._refusals.get(name)
            if self._current() is not True:
                return fallback
            result = method(*args, **kwargs)
            return result if self._current() is True else fallback

        return guarded
