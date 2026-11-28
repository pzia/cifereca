"""Pure passive health projection; no transport, persistence or scheduling."""
from dataclasses import dataclass, replace
from enum import Enum
from collections import deque
from threading import RLock
from contextlib import contextmanager


class Outcome(Enum):
    SUCCESS = "success"
    ATTESTED_EMPTY = "attested_empty"
    UNKNOWN = "unknown"
    NETWORK = "network"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    SUSPECTED_CHANGE = "suspected_change"
    INTERNAL = "internal"


@dataclass(frozen=True)
class Scope:
    provider: str
    instance: str
    account: str
    identity: str
    capability: str


@dataclass(frozen=True)
class Observation:
    outcome: Outcome
    version: str
    observed_at: str
    evidence_ref: str
    capture_id: str | None = None


class PassiveHealth:
    """Bounded projection of already completed read operations.

    Callers must supply exact scope and typed business evidence. No inference
    from HTTP status or empty results. Not a capture store.
    """

    def __init__(self, *, max_scopes=256):
        if type(max_scopes) is not int or max_scopes < 1:
            raise ValueError("invalid scope limit")
        self._limit = max_scopes
        self._enabled = False
        self._rows = {}
        self._lock = RLock()
        self._generation = 0
        self._transitions = deque(maxlen=256)
        self._overflow = False

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("invalid activation")
        with self._lock:
            if enabled != self._enabled:
                self._generation += 1
            self._enabled = enabled
            if not enabled:
                self._rows.clear()
                self._transitions.clear()
                self._overflow = False

    def lease(self):
        """Capture before an operation; stale completions must not be admitted."""
        with self._lock:
            return self._generation if self._enabled else None

    def observe(self, scope, observation, *, lease):
        with self._lock:
            return self._observe(scope, observation, lease)

    def _observe(self, scope, observation, lease):
        if not self._enabled or lease != self._generation:
            return False
        if type(scope) is not Scope or type(observation) is not Observation:
            raise ValueError("invalid observation")
        if not all(type(v) is str and v.strip() == v and len(v) <= 256
                   and all(ch.isprintable() for ch in v)
                   for v in (scope.provider, scope.instance, scope.account,
                             scope.identity, scope.capability)):
            raise ValueError("invalid scope")
        if not scope.provider or not scope.capability:
            raise ValueError("invalid scope")
        if any((scope.instance, scope.account, scope.identity)) and not all(
                (scope.instance, scope.account, scope.identity)):
            raise ValueError("partial scope")
        if not isinstance(observation.outcome, Outcome) or not all(
                type(v) is str and 0 < len(v) <= 256
                for v in (observation.version, observation.observed_at,
                          observation.evidence_ref)):
            raise ValueError("invalid evidence")
        if observation.capture_id is not None:
            raise ValueError('capture must be linked separately')
        if scope not in self._rows:
            if len(self._rows) >= self._limit:
                return False
            self._rows[scope] = (None, None, deque(maxlen=3))
        previous, reference, anomalies = self._rows[scope]
        if observation.outcome in (Outcome.SUCCESS, Outcome.ATTESTED_EMPTY):
            reference = observation
        elif observation.outcome is not Outcome.UNKNOWN:
            anomalies.append(observation)
        self._rows[scope] = (observation, reference, anomalies)
        changed = previous is None or previous.outcome != observation.outcome
        if changed and observation.outcome is not Outcome.UNKNOWN:
            if len(self._transitions) == self._transitions.maxlen:
                self._overflow = True
            self._transitions.append((scope, previous.outcome if previous else None,
                                      observation.outcome, observation.observed_at))
        return changed

    def snapshot(self, scope):
        with self._lock:
            latest, reference, anomalies = self._rows.get(scope, (None, None, ()))
            return latest, reference, tuple(anomalies)

    def link_capture(self, scope, observation_ref, capture_id, *, lease, port, consented=False):
        """Explicitly link an existing completed capture, never start recording.

        The caller supplies its activation-bound vault port. Manual/public
        captures cannot attest an exact private scope.
        """
        from ..plugin_api.capture_vault import CaptureScope, VaultError
        if consented is not True or not all((scope.provider, scope.instance,
                                             scope.account, scope.identity)):
            return False
        with self._lock:
            if not self._enabled or lease != self._generation or scope not in self._rows:
                return False
            latest, reference, anomalies = self._rows[scope]
            if not any(row is not None and row.evidence_ref == observation_ref
                       for row in (latest, reference, *anomalies)):
                return False
        try:
            exact = CaptureScope(scope.provider, scope.instance, scope.account, scope.identity)
            captured = port.read(exact, capture_id)
            summary = captured.summary
            if (summary.scope != exact or summary.capture_id != capture_id
                    or summary.source_session_ref != observation_ref
                    or summary.state != "completed" or not captured.payload):
                return False
        except VaultError:
            return False
        with self._lock:
            if not self._enabled or lease != self._generation or scope not in self._rows:
                return False
            latest, reference, anomalies = self._rows[scope]
            def link(row):
                return (replace(row, capture_id=capture_id)
                        if row is not None and row.evidence_ref == observation_ref else row)
            if not any(row is not None and row.evidence_ref == observation_ref
                       for row in (latest, reference, *anomalies)):
                return False
            self._rows[scope] = (link(latest), link(reference),
                                 deque((link(row) for row in anomalies), maxlen=3))
            return True

    def owns_reference(self, scope, reference):
        with self._lock:
            return _unique_owner(self._rows, self._enabled, scope, reference)

    def notifications_saturated(self):
        with self._lock:
            return self._overflow

    def drain_transitions(self, *, identity_id=None):
        """Bounded, single-consumer transition notifications; no callbacks under lock."""
        with self._lock:
            if identity_id is None:
                rows = tuple(self._transitions)
                self._transitions.clear()
                return rows
            rows = tuple(row for row in self._transitions
                         if not row[0].identity or row[0].identity == identity_id)
            self._transitions = deque(
                (row for row in self._transitions
                 if row[0].identity and row[0].identity != identity_id), maxlen=256)
            return rows

    def inventory(self):
        """Immutable snapshot of observed scopes, not installed providers."""
        with self._lock:
            return tuple((scope, latest, baseline, tuple(anomalies))
                         for scope, (latest, baseline, anomalies) in self._rows.items())

    @contextmanager
    def archive_guard(self, scope, reference):
        """Serialize consented archival against projection revocation."""
        with self._lock:
            if not _unique_owner(self._rows, self._enabled, scope, reference):
                yield None
            else:
                yield self._generation


def _unique_owner(rows, enabled, scope, reference):
    if not enabled:
        return False
    owners = {candidate for candidate, (latest, baseline, anomalies) in rows.items()
              if any(row is not None and row.evidence_ref == reference
                     for row in (latest, baseline, *anomalies))}
    return owners == {scope}
