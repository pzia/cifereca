from contextlib import nullcontext
from types import SimpleNamespace
from cifereca.core.provider_health_inventory import installed_health
from cifereca.core.provider_health_projection import PassiveHealth, Scope, Observation, Outcome


def test_installed_inventory_zero_one_many():
    health = PassiveHealth()
    rows = []
    manager = SimpleNamespace(hold_descriptive_lifecycle=nullcontext, statuses=lambda: rows)
    assert installed_health(manager, health) == ()
    def status(id, enabled, category="provider.library"):
        return SimpleNamespace(descriptor=SimpleNamespace(id=id, type=category),
                               enabled=enabled, state="ready" if enabled else "disabled")
    rows.append(status("a", False))
    assert len(installed_health(manager, health)) == 1
    assert not installed_health(manager, health)[0].enabled
    rows.extend([status("b", True), status("c", True, "provider.collection"),
                 status("ui", True, "ui.page")])
    assert [row.plugin_id for row in installed_health(manager, health)] == ["a", "b", "c"]


def test_transition_deduplication_and_disable():
    health = PassiveHealth()
    health.set_enabled(True)
    scope = Scope("a", "", "", "", "search")
    for outcome in (Outcome.AUTH, Outcome.AUTH, Outcome.SUCCESS, Outcome.SUCCESS):
        health.observe(scope, Observation(outcome, "1", "now", "ref"), lease=health.lease())
    assert [row[2] for row in health.drain_transitions()] == [Outcome.AUTH, Outcome.SUCCESS]
    assert health.drain_transitions() == ()
    health.observe(scope, Observation(Outcome.AUTH, "1", "now", "ref"), lease=health.lease())
    health.set_enabled(False)
    assert health.drain_transitions() == ()
