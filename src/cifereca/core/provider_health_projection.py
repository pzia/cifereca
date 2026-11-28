"""Pure passive health projection; no transport, persistence or scheduling."""
from dataclasses import dataclass
from enum import Enum
from collections import deque


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

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("invalid activation")
        self._enabled = enabled
        if not enabled:
            self._rows.clear()

    def observe(self, scope, observation):
        if not self._enabled:
            return False
        if type(scope) is not Scope or type(observation) is not Observation:
            raise ValueError("invalid observation")
        if not all(type(v) is str and v.strip() == v and len(v) <= 256
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
        return previous is None or previous.outcome != observation.outcome

    def snapshot(self, scope):
        latest, reference, anomalies = self._rows.get(scope, (None, None, ()))
        return latest, reference, tuple(anomalies)
