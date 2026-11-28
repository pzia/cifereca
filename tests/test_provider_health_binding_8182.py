from cifereca.core.provider_health_projection import PassiveHealth, Scope, Observation, Outcome


def test_reference_binding_unique_and_revoked():
    health = PassiveHealth()
    health.set_enabled(True)
    lease = health.lease()
    a = Scope("fixture", "instance", "a", "owner", "search")
    b = Scope("fixture", "instance", "b", "owner", "search")
    row = Observation(Outcome.AUTH, "1", "now", "ref")
    health.observe(a, row, lease=lease)
    assert health.owns_reference(a, "ref")
    assert not health.owns_reference(b, "ref")
    assert len(health.inventory()) == 1
    health.observe(b, row, lease=lease)
    assert not health.owns_reference(a, "ref")
    assert not health.owns_reference(b, "ref")
    health.set_enabled(False)
    assert not health.owns_reference(a, "ref")
    assert not health.inventory()
