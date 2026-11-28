"""Pure passive health projection; no transport, persistence or scheduling."""
from dataclasses import dataclass, replace
from enum import Enum
from collections import deque
from threading import RLock
from contextlib import contextmanager
from uuid import uuid4


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
        self._witnesses = {}
        self._lock = RLock()
        self._archive_lock = RLock()
        self._generation = 0
        self._diagnostic_nonce = uuid4().hex
        self._transitions = deque(maxlen=256)
        self._overflow = False

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("invalid activation")
        with self._archive_lock, self._lock:
            if enabled != self._enabled:
                self._generation += 1
            self._enabled = enabled
            if not enabled:
                self._rows.clear()
                self._witnesses.clear()
                self._transitions.clear()
                self._overflow = False

    def lease(self):
        """Capture before an operation; stale completions must not be admitted."""
        with self._lock:
            return self._generation if self._enabled else None

    def diagnostic_generation(self):
        """Opaque instance/activation identity, independent of evidence tokens."""
        with self._lock:
            return (self._diagnostic_nonce, self._generation) if self._enabled else None

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

    def retain_book_witnesses(self, scope, observation, books, *, lease):
        """Keep at most three exact, versioned book coordinates per provider.

        Private candidates remain bound to the admitting scope; no title,
        credential, URL or authorization is shared with another account.
        Absence in a later result is not evidence of disappearance.
        """
        if type(books) is not tuple or type(scope) is not Scope or type(observation) is not Observation:
            return
        with self._lock:
            if (not self._enabled or lease != self._generation
                    or scope.capability != "search" or scope not in self._rows
                    or self._rows[scope][0] != observation
                    or observation.outcome is not Outcome.SUCCESS):
                return
            rows = self._witnesses.setdefault(scope.provider, [])
            for book in books:
                if (type(book) is not tuple or len(book) != 2
                        or not all(type(v) is str and 0 < len(v) <= 256
                                   and all(c.isprintable() for c in v) for v in book)):
                    continue
                # Renew only a positively re-observed witness in its original
                # scope. A missing result never evicts an existing witness.
                index = next((i for i, (owner, old_book, _, _) in enumerate(rows)
                              if owner == scope and old_book == book), None)
                if index is not None:
                    rows[index] = (scope, book, observation.version, observation.evidence_ref)
                    continue
                if len(rows) >= 3 or any(old_book == book for _, old_book, _, _ in rows):
                    continue
                rows.append((scope, book, observation.version, observation.evidence_ref))

    def book_witnesses(self, scope):
        """Read only witnesses admitted under the identical search scope."""
        with self._lock:
            if not self._enabled or type(scope) is not Scope:
                return ()
            return tuple(row for row in self._witnesses.get(scope.provider, ())
                         if row[0] == scope)

    def retained_observations(self, scope):
        latest, baseline, anomalies = self.snapshot(scope)
        return tuple(row for row in (latest, baseline, *anomalies) if row is not None)

    def restore_history(self, rows, *, lease):
        """Rehydrate historical evidence only, without notifications or latest PASS."""
        with self._lock:
            if not self._enabled or lease != self._generation:
                return 0
            count = 0
            for scope, obs, books in rows:
                if (type(scope) is not Scope or type(obs) is not Observation
                        or not all(type(v) is str and 0 < len(v) <= 256
                                   for v in (scope.provider, scope.instance, scope.account,
                                             scope.identity, scope.capability, obs.version,
                                             obs.observed_at, obs.evidence_ref, obs.capture_id))
                        or obs.outcome is Outcome.UNKNOWN):
                    continue
                if scope not in self._rows:
                    if len(self._rows) >= self._limit:
                        continue
                    self._rows[scope] = (None, None, deque(maxlen=3))
                latest, baseline, anomalies = self._rows[scope]
                if any(row.evidence_ref == obs.evidence_ref
                       for row in self.retained_observations(scope)):
                    continue
                if any(row.evidence_ref == obs.evidence_ref
                       for candidate in self._rows if candidate != scope
                       for row in self.retained_observations(candidate)):
                    continue
                if obs.outcome in (Outcome.SUCCESS, Outcome.ATTESTED_EMPTY):
                    if baseline is not None:
                        continue
                    baseline = obs
                elif len(anomalies) < 3:
                    anomalies.appendleft(obs)
                else:
                    continue
                self._rows[scope] = (latest, baseline, anomalies)
                if (scope.capability == "search" and obs.outcome is Outcome.SUCCESS
                        and type(books) is tuple and len(books) <= 3):
                    witnesses = self._witnesses.setdefault(scope.provider, [])
                    for book in books:
                        if len(witnesses) >= 3:
                            break
                        if (type(book) is tuple and len(book) == 2
                                and all(type(value) is str and 0 < len(value) <= 256
                                        and value.isprintable() for value in book)
                                and not any(existing == book for _, existing, _, _ in witnesses)):
                            witnesses.append((scope, book, obs.version, obs.evidence_ref))
                count += 1
            return count

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
            if (not self._enabled or lease != self._generation
                    or not _unique_owner(self._rows, self._enabled, scope, observation_ref)):
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
            if (not self._enabled or lease != self._generation
                    or not _unique_owner(self._rows, self._enabled, scope, observation_ref)):
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

    def unlink_capture(self, scope, observation_ref, capture_id):
        """Remove only a failed archival link; never discard observations."""
        with self._lock:
            if scope not in self._rows:
                return
            latest, baseline, anomalies = self._rows[scope]
            def unlink(row):
                if (row is not None and row.evidence_ref == observation_ref
                        and row.capture_id == capture_id):
                    return replace(row, capture_id=None)
                return row
            self._rows[scope] = (unlink(latest), unlink(baseline),
                                 deque((unlink(row) for row in anomalies), maxlen=3))

    def owns_reference(self, scope, reference):
        return self.has_unique_evidence(scope, reference)

    def has_unique_evidence(self, scope, evidence_ref):
        """Whether one exact scope owns this retained observation token."""
        with self._lock:
            return _unique_owner(self._rows, self._enabled, scope, evidence_ref)

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
        # Never retain the projection lock across vault, storage or HTTP I/O.
        # Only disable/enable and archival acquire the outer gate.
        with self._archive_lock:
            with self._lock:
                lease = (self._generation if _unique_owner(
                    self._rows, self._enabled, scope, reference) else None)
            yield lease


def _unique_owner(rows, enabled, scope, reference):
    if not enabled:
        return False
    owners = {candidate for candidate, (latest, baseline, anomalies) in rows.items()
              if any(row is not None and row.evidence_ref == reference
                     for row in (latest, baseline, *anomalies))}
    return owners == {scope}
