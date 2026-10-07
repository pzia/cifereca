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


class Service:
    def __init__(self):
        self.storage = Storage()
        self.runtime = Runtime()
        self.identities = [NS(identity_id="alice")]

    def identity_by_id(self, identity_id):
        return next((row for row in self.identities if row.identity_id == identity_id), None)

    def _kobo_adapter(self):
        return self.runtime

    def kobo_profiles(self):
        return [dict(row) for row in self.runtime.rows]


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
