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


def test_page_health_projection_exact_identity_and_no_foreign_evidence():
    from cifereca.brokers import DiagnosticsBrokerFactory
    health = PassiveHealth()
    health.set_enabled(True)
    caps = SimpleNamespace(search=True)
    public = SimpleNamespace(descriptor=SimpleNamespace(
        id="public", capabilities=caps, credential_fields=()))
    private = SimpleNamespace(descriptor=SimpleNamespace(
        id="private", capabilities=caps, credential_fields=("login",)))
    owners = [SimpleNamespace(identity_id=owner) for owner in ("a", "b")]
    accounts = [SimpleNamespace(provider_id="private", identity_id=owner,
                  credential_id="instance-" + owner, unique_id="account-" + owner)
                for owner in ("a", "b")]
    service = SimpleNamespace(
        _provider_health=health, identities=owners, current_identity=owners[0],
        accounts_for_identity=lambda owner: [a for a in accounts if a.identity_id == owner],
        identity_for_connection=lambda provider, instance: next(
            row for row in owners if instance == "instance-" + row.identity_id),
        registry=SimpleNamespace(all=lambda: [public, private]),
        _provider_enabled_for_identity=lambda provider, owner: True)
    for scope, ref in ((Scope("public", "", "", "", "search"), "public-ref"),
                       (Scope("private", "instance-a", "account-a", "a", "search"), "a-ref"),
                       (Scope("private", "instance-b", "account-b", "b", "search"), "b-ref")):
        health.observe(scope, Observation(Outcome.AUTH, "1", "now", ref), lease=health.lease())
    factory = DiagnosticsBrokerFactory(service)
    assert not hasattr(factory.for_plugin("other"), "provider_health_observations")
    assert not hasattr(factory.for_plugin("other"), "provider_health_active_observations")
    assert not hasattr(factory.for_plugin("other"), "provider_health_selection_current")
    page = factory.for_plugin("ui.page.diagnostics")
    assert page.provider_health_observations("") == ()
    assert page.provider_health_observations("b") == ()
    rows = page.provider_health_observations("a")
    assert {row["identity_id"] for row in rows} == {"", "a"}
    assert {row["evidence_ref"] for row in rows} == {None, "a-ref"}
    assert all(row["outcome"] == "auth" for row in rows)
    from cifereca.plugin_api.capture_vault import CaptureScope
    exact = CaptureScope("private", "instance-a", "account-a", "a")
    selected = next(row for row in rows if row["evidence_ref"] == "a-ref")
    generation = selected["generation"]
    assert page.provider_health_active_observations() == rows
    def valid(token=generation, scope=exact, capture_id=None, capability="search"):
        return page.provider_health_selection_current(
            scope, capability, "a-ref", capture_id, token)
    assert valid()
    assert not valid(scope=CaptureScope())
    assert not valid(capability="loans")
    assert not valid(capture_id="unlinked")
    assert not valid(token=tuple())
    # Account/identity round trip without any source reconfiguration.
    service.current_identity = owners[1]
    service._account_context_epoch = 1
    assert not valid()
    service.current_identity = owners[0]
    service._account_context_epoch = 2
    assert not valid()
    refreshed = next(row for row in page.provider_health_active_observations()
                     if row["evidence_ref"] == "a-ref")["generation"]
    assert valid(token=refreshed)
    # A new projection with the same lease and same evidence cannot revive consent.
    replacement = PassiveHealth()
    replacement.set_enabled(True)
    replacement.observe(Scope("private", "instance-a", "account-a", "a", "search"),
                        Observation(Outcome.AUTH, "1", "now", "a-ref"),
                        lease=replacement.lease())
    service._provider_health = replacement
    assert not valid(token=refreshed)
    service._provider_health = health
    # Link and unlink alter the exact selection independently of the generation.
    from cifereca.plugin_api.capture_vault import CaptureSummary, CapturedSession
    summary = CaptureSummary("linked", exact, "a-ref", "completed", "now", 1)
    port = SimpleNamespace(read=lambda scope, cid: CapturedSession(summary, b"x"))
    private_scope = Scope("private", "instance-a", "account-a", "a", "search")
    assert health.link_capture(private_scope, "a-ref", "linked", lease=health.lease(),
                               port=port, consented=True)
    assert not valid(token=refreshed)
    assert valid(token=refreshed, capture_id="linked")
    health.unlink_capture(private_scope, "a-ref", "linked")
    assert not valid(token=refreshed, capture_id="linked")
    # A reference shared with another scope cannot authorize an archive.
    health.observe(Scope("private", "instance-b", "account-b", "b", "search"),
                   Observation(Outcome.AUTH, "1", "now", "a-ref"), lease=health.lease())
    assert all(row["evidence_ref"] is None for row in page.provider_health_observations("a"))
    service.current_identity = owners[1]
    assert page.provider_health_observations("a") == ()
    # Revocation during projection discards the whole snapshot.
    original = service.accounts_for_identity
    def revoked(owner):
        service._source_context_epoch = 1
        return original(owner)
    service.accounts_for_identity = revoked
    assert page.provider_health_observations("b") == ()
    health.set_enabled(False)
    assert page.provider_health_observations("b") == ()
