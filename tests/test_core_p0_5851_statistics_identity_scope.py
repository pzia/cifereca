"""P0 #5851: statistics are not an implicit selected-profile read.

The public plugin broker and direct Core producer reject malformed or hostile
identity coordinates before SQLite scans, even if a legacy row has NULL
identity_id. Valid native requests keep the existing persisted-only contract.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import IdentityBrokerImpl
from cifereca.core.sync_projection import action_statistics, identity_statistics


class HostileId(str):
    def __eq__(self, _other):
        raise AssertionError("untrusted identity equality")

    def strip(self, *_args):
        raise AssertionError("untrusted identity strip")


class TrapStorage:
    def __getattr__(self, name):
        raise AssertionError(f"SQLite read happened before identity admission: {name}")


class TrapCore:
    def __init__(self):
        self.storage = TrapStorage()
        self.calls = []

    def identity_statistics(self, identity_id, *, window_seconds):
        self.calls.append((identity_id, window_seconds))
        return {"identity_id": identity_id}


@pytest.mark.parametrize("invalid", [
    None, "", " ", "\t", 17, True,
    HostileId("profile-A"),
    " profile-A", "profile-A ", "profile-A\x00",
    "x" * 257,
])
def test_public_broker_refuses_implicit_or_hostile_statistics_identity(invalid):
    core = TrapCore()
    broker = IdentityBrokerImpl(core)
    with pytest.raises(ValueError, match="identity_id_required"):
        broker.statistics(invalid)
    assert core.calls == []


@pytest.mark.parametrize("invalid", [
    None, "", " ", "\t", 17, True,
    HostileId("profile-A"),
    " profile-A", "profile-A ", "profile-A\x00",
    "x" * 257,
])
def test_direct_core_statistic_rejects_before_any_sqlite_read(invalid):
    with pytest.raises(ValueError, match="identity_id_required"):
        identity_statistics(NS(storage=TrapStorage()), invalid)


@pytest.mark.parametrize("invalid", [
    None, "", " ", "\t", 17, True,
    HostileId("profile-A"),
    " profile-A", "profile-A ", "profile-A\x00",
    "x" * 257,
])
def test_direct_action_counters_cannot_read_all_profiles(invalid):
    # action_event_counts(identity_id=None) is an all-profile SQL aggregate.
    # The public Core facade must reject this before opening a connection.
    with pytest.raises(ValueError, match="identity_id_required"):
        action_statistics(NS(storage=TrapStorage()), invalid)


def test_two_independent_plugin_clients_keep_exact_identity_coordinate():
    core = TrapCore()
    broker = IdentityBrokerImpl(core)
    assert broker.statistics("profile-A", window_seconds=3600) == {
        "identity_id": "profile-A",
    }
    assert broker.statistics("profile-B", window_seconds=7200) == {
        "identity_id": "profile-B",
    }
    assert core.calls == [("profile-A", 3600), ("profile-B", 7200)]


def test_native_legacy_identity_short_values_remain_accepted():
    core = TrapCore()
    broker = IdentityBrokerImpl(core)
    assert broker.statistics("A") == {"identity_id": "A"}
    assert core.calls == [("A", 2592000)]
