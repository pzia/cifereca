"""#3171: source-level revocation and ON->OFF->ON generation fences."""
from __future__ import annotations

import json

from cifereca.core.convergence_execution import _source_policy_generation_current
from cifereca.core.source_sync_consent import policy_key
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from cifereca.storage import Storage


def test_revocation_and_reenable_do_not_resurrect_old_get(tmp_path):
    store = Storage(tmp_path / "consent.sqlite")
    scope = ProviderRightsScope("profile", "synthetic", "credential", "buyer")
    def write(enabled, revision):
        store.metadata_set(policy_key(scope), json.dumps({
            "schema": "cifereca.source-sync-consent/1",
            "scope": ["profile", "synthetic", "credential", "buyer"],
            "enabled": enabled, "revision": revision,
        }))
    old, new = "a" * 32, "b" * 32
    try:
        assert not _source_policy_generation_current(store, scope, old)
        write(True, old)
        assert _source_policy_generation_current(store, scope, old)
        write(False, new)
        assert not _source_policy_generation_current(store, scope, old)
        write(True, new)
        assert not _source_policy_generation_current(store, scope, old)
        assert _source_policy_generation_current(store, scope, new)
        store.metadata_set(policy_key(scope), "{malformed")
        assert not _source_policy_generation_current(store, scope, new)
    finally:
        store.close()


def test_storage_failure_fails_closed_without_leaking_details():
    class BrokenStorage:
        def metadata_get(self, key):
            raise RuntimeError("private credentials")
    scope = ProviderRightsScope("profile", "synthetic", "credential", "buyer")
    assert not _source_policy_generation_current(BrokenStorage(), scope, "a" * 32)
