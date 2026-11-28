"""Pure projection tests; no provider or network access."""
import pytest
from cifereca.core.provider_health_projection import (
    Observation, Outcome, PassiveHealth, Scope,
)

A = Scope("fixture", "i", "a", "identity", "search")
B = Scope("fixture", "i", "b", "identity", "search")


def obs(outcome, n=0):
    return Observation(outcome, "1", str(n), "proof-" + str(n))


def test_disabled_and_unknown_are_not_success():
    health = PassiveHealth()
    assert not health.observe(A, obs(Outcome.SUCCESS))
    health.set_enabled(True)
    assert health.observe(A, obs(Outcome.UNKNOWN))
    assert health.snapshot(A)[1] is None
    assert health.observe(A, obs(Outcome.ATTESTED_EMPTY))
    assert health.snapshot(A)[1].outcome is Outcome.ATTESTED_EMPTY
    health.set_enabled(False)
    assert health.snapshot(A) == (None, None, ())


def test_retention_and_scope_isolation():
    health = PassiveHealth()
    health.set_enabled(True)
    health.observe(A, obs(Outcome.SUCCESS))
    for n in range(5):
        assert health.observe(A, obs(Outcome.NETWORK, n)) == (n == 0)
    latest, reference, anomalies = health.snapshot(A)
    assert latest.outcome is Outcome.NETWORK
    assert reference.outcome is Outcome.SUCCESS
    assert [a.observed_at for a in anomalies] == ["2", "3", "4"]
    assert health.snapshot(B) == (None, None, ())


def test_public_scope_and_bounded_admission():
    health = PassiveHealth(max_scopes=1)
    health.set_enabled(True)
    public = Scope("public", "", "", "", "search")
    assert health.observe(public, obs(Outcome.SUCCESS))
    assert not health.observe(A, obs(Outcome.AUTH))
    with pytest.raises(ValueError):
        health.observe(Scope("public", "i", "", "", "search"), obs(Outcome.SUCCESS))
