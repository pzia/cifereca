from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import AdeptProfileBrokerImpl, build_public_brokers
from cifereca.plugin_api import AdeptProfileBroker, AdeptProfileRef, AttentionRequired


class Storage:
    def __init__(self):
        self.bound = {}

    def adept_profile_binding_get(self, scope_type, scope_id):
        return self.bound.get((scope_type, scope_id))

    def adept_profile_binding_set(self, scope_type, scope_id, profile_id):
        self.bound[(scope_type, scope_id)] = profile_id


class Runtime:
    def __init__(self):
        self.rows = []
        self.capability_refreshes = 0
        self.provision_calls = []

    def provision_profile(self, name, username):
        self.provision_calls.append((name, username))
        row = {
            "profile_id": "stable-" + name,
            "name": name,
            "complete": True,
            "fetch_capable": True,
            "provisioning": {"acsm_fetch_ready": True},
        }
        self.rows = [row]
        return {"profile": dict(row)}

    def capabilities(self):
        self.capability_refreshes += 1
        return {"capabilities": {"adept": {"fetch_capable_profiles": [row["name"] for row in self.rows]}}}

    def profiles(self):
        return {"profiles": [dict(row) for row in self.rows]}


class Service:
    def __init__(self):
        self.storage = Storage()
        self.runtime = Runtime()
        self.identities = [NS(identity_id="alice")]

    def identity_by_id(self, identity_id):
        return next((row for row in self.identities if row.identity_id == identity_id), None)

    def _required_plugin_service(self, plugin_id):
        assert plugin_id == "runtime.kobo-adept"
        return self.runtime


def test_public_api_exports_adept_profile_contract():
    assert AdeptProfileRef
    assert AdeptProfileBroker


def test_public_broker_registry_contains_adept_profiles():
    brokers = build_public_brokers(Service())
    assert isinstance(brokers["adept.profiles"], AdeptProfileBroker)


def test_provision_refreshes_capabilities_and_binds_stable_profile_id():
    service = Service()
    broker = AdeptProfileBrokerImpl(service)

    profile = broker.provision("alexandre", "a@example.test", identity_id="alice")

    assert profile == AdeptProfileRef(
        profile_id="stable-alexandre",
        name="alexandre",
        complete=True,
        fetch_capable=True,
        acsm_fetch_ready=True,
    )
    assert service.runtime.provision_calls == [("alexandre", "a@example.test")]
    assert service.runtime.capability_refreshes == 1
    assert service.storage.bound[("identity", "alice")] == "stable-alexandre"
    assert broker.resolve_name("stable-alexandre") == "alexandre"
    assert broker.bound("alice") == profile


def test_bind_refuses_profile_without_acsm_readiness():
    service = Service()
    service.runtime.rows = [{
        "profile_id": "stable-incomplete",
        "name": "incomplete",
        "complete": True,
        "fetch_capable": False,
        "provisioning": {"acsm_fetch_ready": False},
    }]
    broker = AdeptProfileBrokerImpl(service)

    with pytest.raises(AttentionRequired) as exc:
        broker.bind("alice", "stable-incomplete")

    assert exc.value.code == "adept_profile_not_ready"
    assert service.storage.bound == {}


def test_profile_without_stable_id_is_never_persisted_by_name():
    service = Service()
    service.runtime.rows = [{
        "name": "alexandre",
        "complete": True,
        "fetch_capable": True,
        "provisioning": {"acsm_fetch_ready": True},
    }]
    broker = AdeptProfileBrokerImpl(service)

    with pytest.raises(AttentionRequired) as exc:
        broker.bind("alice", "alexandre")

    assert exc.value.code == "adept_profile_stable_id_required"
    assert service.storage.bound == {}


def test_broker_uses_only_installed_service_and_never_calls_legacy_adapter():
    service = Service()
    def forbidden():
        pytest.fail("legacy adapter must not be invoked")
    service._kobo_adapter = forbidden
    service.kobo_profiles = forbidden
    broker = AdeptProfileBrokerImpl(service)
    assert broker.profiles() == ()
    assert broker.provision("reader", "reader@example.test", identity_id="alice").profile_id == "stable-reader"


def test_missing_installed_runtime_fails_closed_without_legacy_fallback():
    service = Service()
    def unavailable(_plugin_id):
        raise AttentionRequired("runtime required", code="plugin_required")
    service._required_plugin_service = unavailable
    service._kobo_adapter = lambda: pytest.fail("legacy runtime fallback")
    service.kobo_profiles = lambda: pytest.fail("legacy profile fallback")
    broker = AdeptProfileBrokerImpl(service)
    with pytest.raises(AttentionRequired) as exc:
        broker.profiles()
    assert exc.value.code == "plugin_required"
    with pytest.raises(AttentionRequired) as exc:
        broker.provision("reader", "reader@example.test", identity_id="alice")
    assert exc.value.code == "plugin_required"


@pytest.mark.parametrize("payload", [None, [], {"profiles": None}, {"profiles": "not-a-list"}])
def test_malformed_runtime_profile_rows_never_gain_binding(payload):
    service = Service()
    service.runtime.profiles = lambda: payload
    broker = AdeptProfileBrokerImpl(service)
    assert broker.profiles() == ()
    with pytest.raises(AttentionRequired) as exc:
        broker.bind("alice", "not-proven")
    assert exc.value.code == "adept_origin_profile_missing"
    assert service.storage.bound == {}


@pytest.mark.parametrize("mode", ["absent", "ambiguous", "mismatched_id", "unready"])
def test_provision_does_not_bind_unattested_or_ambiguous_runtime_profile(mode):
    service = Service()
    runtime = service.runtime
    original = runtime.provision_profile

    def provision(name, username):
        response = original(name, username)
        if mode == "absent":
            runtime.rows = []
        elif mode == "ambiguous":
            runtime.rows.append(dict(runtime.rows[0], profile_id="another-stable"))
        elif mode == "mismatched_id":
            runtime.rows[0]["profile_id"] = "substituted-profile"
        elif mode == "unready":
            runtime.rows[0]["provisioning"] = {"acsm_fetch_ready": False}
        return response

    runtime.provision_profile = provision
    with pytest.raises(AttentionRequired) as exc:
        AdeptProfileBrokerImpl(service).provision(
            "reader", "reader@example.test", identity_id="alice",
        )
    assert exc.value.code == "adept_profile_provisioning_incomplete"
    assert service.storage.bound == {}


def test_provision_refuses_runtime_rebind_before_persisting_profile():
    service = Service()
    original = service.runtime
    replacement = Runtime()

    def provision(name, username):
        response = Runtime.provision_profile(original, name, username)
        service.runtime = replacement
        replacement.rows = [dict(row) for row in original.rows]
        return response

    original.provision_profile = provision
    with pytest.raises(AttentionRequired) as exc:
        AdeptProfileBrokerImpl(service).provision(
            "reader", "reader@example.test", identity_id="alice",
        )
    assert exc.value.code == "adept_profile_provisioning_incomplete"
    assert service.storage.bound == {}


def test_provision_name_only_acknowledgement_requires_unique_stable_inventory():
    service = Service()

    def provision(name, username):
        service.runtime.rows = [{
            "profile_id": "stable-reader", "name": name,
            "complete": True, "fetch_capable": True,
            "provisioning": {"acsm_fetch_ready": True},
        }]
        return {"profile": {"name": name}}

    service.runtime.provision_profile = provision
    profile = AdeptProfileBrokerImpl(service).provision(
        "reader", "reader@example.test", identity_id="alice",
    )
    assert profile.profile_id == "stable-reader"
    assert service.storage.bound == {("identity", "alice"): "stable-reader"}
