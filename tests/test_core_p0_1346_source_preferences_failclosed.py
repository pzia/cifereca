"""P0 #1346: source preferences and routing must fail closed before provider IO."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account
from cifereca.storage import Storage
from tests.test_core_0978_source_state import fake_broker


@pytest.mark.parametrize("stored", [
    " PRIVATE:bearer?token=SECRET", "maybe", "", "2", "null",
    "https://private.invalid/?token=SECRET", "[]",
    "off?force=yes", "true#unsafe", "no\ntrue",
])
def test_malformed_persisted_source_preference_blocks_network(tmp_path, stored):
    storage = Storage(tmp_path / "sync-preferences.sqlite3")
    try:
        broker = fake_broker(storage)
        sid = broker.sources()[0].source_id
        key = broker._preference_key(sid)
        storage.metadata_set(key, stored)
        assert broker.sources()[0].enabled is False
        called = []
        broker.service.synchronize_account_source = lambda *a, **k: called.append((a, k))
        result = broker.synchronize_source(sid)
        assert result.changed == 0 and result.facts.get("skipped") is True
        assert not called
        assert stored not in repr(result)
        assert storage.metadata_get("sync.source.state." + sid) is None
    finally:
        storage.close()


@pytest.mark.parametrize("stored,enabled", [
    ("1", True), ("true", True), ("TRUE", True),
    (" yes ", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
])
def test_legacy_explicit_preferences_remain_readable(tmp_path, stored, enabled):
    storage = Storage(tmp_path / "compat.sqlite3")
    try:
        broker = fake_broker(storage)
        sid = broker.sources()[0].source_id
        storage.metadata_set(broker._preference_key(sid), stored)
        assert broker.sources()[0].enabled is enabled
    finally:
        storage.close()


def test_unset_default_stays_enabled_for_existing_inventory_sources(tmp_path):
    storage = Storage(tmp_path / "unset.sqlite3")
    try:
        broker = fake_broker(storage)
        assert broker.sources()[0].enabled is True
    finally:
        storage.close()


@pytest.mark.parametrize("invalid", [None, "true", 1, 0, [], object()])
def test_setting_source_preference_requires_real_boolean(tmp_path, invalid):
    storage = Storage(tmp_path / "invalid-bool.sqlite3")
    try:
        broker = fake_broker(storage)
        sid = broker.sources()[0].source_id
        with pytest.raises(ValueError, match="invalid source enabled preference"):
            broker.set_enabled(sid, invalid)
        assert storage.metadata_get(broker._preference_key(sid)) is None
        broker.set_enabled(sid, False)
        assert broker.sources()[0].enabled is False
        broker.set_enabled(sid, True)
        assert broker.sources()[0].enabled is True
    finally:
        storage.close()


def test_ambiguous_same_source_bindings_reject_single_source_sync_and_toggle(tmp_path):
    storage = Storage(tmp_path / "ambiguous.sqlite3")
    try:
        broker = fake_broker(storage)
        account = broker.service.accounts[0]
        # This models a corrupt/imported duplicate binding of the *same*
        # provider/credential/account/identity tuple, not just a homonym.
        broker.service.accounts = [account, account]
        sid = broker._source_id(account)
        called = []
        broker.service.synchronize_account_source = lambda *a, **k: called.append(a)
        outcome = broker.synchronize_source(sid)
        assert outcome.ok is False
        assert outcome.facts == {"reason": "unknown_source"}
        assert not called
        with pytest.raises(ValueError, match="ambiguous sync source"):
            broker.set_enabled(sid, True)
        assert storage.metadata_get(broker._preference_key(sid)) is None
        assert storage.metadata_get("sync.source.state." + sid) is None
    finally:
        storage.close()
