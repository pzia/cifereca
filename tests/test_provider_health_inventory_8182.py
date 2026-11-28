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


def test_registered_unobserved_private_and_public():
    from cifereca.core.provider_health_inventory import registered_health
    health = PassiveHealth()
    caps = SimpleNamespace(search=True, loans=True)
    public = SimpleNamespace(descriptor=SimpleNamespace(id="public", capabilities=caps, credential_fields=()))
    private = SimpleNamespace(descriptor=SimpleNamespace(id="private", capabilities=caps, credential_fields=("login",)))
    account = SimpleNamespace(provider_id="private", identity_id="owner", credential_id="instance", unique_id="account")
    service = SimpleNamespace(identities=[SimpleNamespace(identity_id="owner")],
        accounts_for_identity=lambda owner: [account], registry=SimpleNamespace(all=lambda: [public, private]),
        _provider_enabled_for_identity=lambda provider, owner: provider == "public")
    rows = registered_health(service, health, "owner")
    assert len(rows) == 4
    assert all(row[2] is None for row in rows)
    assert {row[0].account for row in rows} == {"", "account"}
    assert registered_health(service, health, "foreign") == ()


def test_notifications_preserve_foreign_identity():
    health = PassiveHealth()
    health.set_enabled(True)
    for owner in ("a", "b"):
        scope = Scope("fixture", "instance", owner, owner, "search")
        health.observe(scope, Observation(Outcome.AUTH, "1", "now", owner),
                       lease=health.lease())
    assert [row[0].identity for row in health.drain_transitions(identity_id="a")] == ["a"]
    assert health.drain_transitions(identity_id="a") == ()
    assert [row[0].identity for row in health.drain_transitions(identity_id="b")] == ["b"]


def test_cached_disabled_collection(monkeypatch):
    from cifereca.core.provider_health_inventory import cached_virtual_health
    from cifereca.core.provider_collection_cache import ProviderCollectionSnapshotStore
    parent = SimpleNamespace(descriptor=SimpleNamespace(id="parent", type="provider.collection"), enabled=False)
    manager = SimpleNamespace(hold_descriptive_lifecycle=nullcontext, statuses=lambda: [parent])
    child = SimpleNamespace(plugin_id="virtual", capabilities=("catalog.search",), status="ready")
    monkeypatch.setattr(ProviderCollectionSnapshotStore, "load",
                        lambda self, parent: SimpleNamespace(children=(child,)))
    assert cached_virtual_health(manager, object()) == (
        ("virtual", "parent", False, ("catalog.search",), "ready"),)


def test_health_count_deduplicates_cached_children():
    from cifereca.core.provider_health_inventory import provider_health_count
    installed = [SimpleNamespace(plugin_id="parent")]
    registered = [(Scope("child", "", "", "", "search"), True, None)]
    cached = [("child", "parent", True, (), "ready"),
              ("disabled", "parent", False, (), "ready")]
    assert provider_health_count(installed, registered, cached) == 3


def test_notification_saturation_is_explicit():
    health = PassiveHealth()
    health.set_enabled(True)
    scope = Scope("fixture", "", "", "", "search")
    for n in range(257):
        outcome = Outcome.AUTH if n % 2 else Outcome.SUCCESS
        health.observe(scope, Observation(outcome, "1", "now", str(n)), lease=health.lease())
    assert health.notifications_saturated()
    assert len(health.drain_transitions()) == 256
    assert health.notifications_saturated()
    health.set_enabled(False)
    assert not health.notifications_saturated()
