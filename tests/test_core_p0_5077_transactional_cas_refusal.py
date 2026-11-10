"""P0 #5077: an unreadable exact CAS is never legacy automatic consent."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import source_auto_consent_bridge as bridge
from cifereca.core.source_convergence import SourceKey
from cifereca.core.source_sync_consent import policy_key
from cifereca.plugin_api import ProviderRightsScope
from cifereca.storage import Storage


def source():
    return SourceKey(
        ProviderRightsScope("reader", "kobo", "card", "borrower"),
        "edition", "right", "purchase", "owned",
    )


def test_unreadable_exact_cas_never_falls_back_to_prior_legacy_on(
    tmp_path, monkeypatch,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        key = source()
        sid = bridge._source_id(key.scope)
        storage.metadata_set("sync.source.materialization.on_start." + sid, "1")
        with storage._lock, storage._connect() as db:
            assert bridge.toggle_state(db, key) == "on"
            def unreadable(_db, _scope):
                raise RuntimeError("private SQL exception with upstream token")

            monkeypatch.setattr(bridge, "policy_in_transaction", unreadable)
            assert bridge.toggle_state(db, key) == "off"
        # Neither the old ON nor the malformed CAS is rewritten.
        assert storage.metadata_get(
            "sync.source.materialization.on_start." + sid
        ) == "1"
        assert storage.metadata_get(policy_key(key.scope)) is None
    finally:
        storage.close()


def test_failed_inventory_enablement_read_refuses_configured_cas(
    tmp_path, monkeypatch,
):
    import json
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        key = source()
        sid = bridge._source_id(key.scope)
        storage.metadata_set("sync.source.materialization.on_start." + sid, "1")
        storage.metadata_set("sync.source.enabled." + sid, "1")
        storage.metadata_set(policy_key(key.scope), json.dumps({
            "schema": "cifereca.source-sync-consent/1",
            "scope": list((
                key.scope.identity_id, key.scope.provider_id,
                key.scope.credential_id, key.scope.account_id,
            )),
            "enabled": True, "revision": "a" * 32,
        }))
        with storage._lock, storage._connect() as db:
            assert bridge.toggle_state(db, key) == "on"
            original_execute = db.execute
            class BrokenEnablement:
                def execute(self, statement, args=()):
                    if args and args[0] == "sync.source.enabled." + sid:
                        raise OSError("private unreadable enabled source row")
                    return original_execute(statement, args)

            assert bridge.toggle_state(BrokenEnablement(), key) == "off"
            assert bridge.toggle_state(db, key) == "on"
    finally:
        storage.close()
