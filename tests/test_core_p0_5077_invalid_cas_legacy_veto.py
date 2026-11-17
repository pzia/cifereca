"""#5077/#5975: corrupt CAS cannot revive historical source-wide consent.

SQLite only; no provider, ADEPT, or remote GET.  These negative contracts
must survive removal of the provider-specific compatibility readers.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core import source_auto_consent_bridge as bridge
from cifereca.core.source_convergence import SourceKey
from cifereca.core.source_sync_consent import (
    policy_key, read_policy, source_enablement_key,
)
from cifereca.plugin_api import ProviderRightsScope
from cifereca.storage import Storage


@pytest.mark.parametrize("provider,kind,index", [
    ("numilog", "purchase", 0),
    ("kobo", "purchase", 2),
    ("bnf-pret", "loan", 1),
    ("fictional", "purchase", 0),
])
@pytest.mark.parametrize("cas_failure", [
    "malformed-json",
    "wrong-scope",
    "duplicate-field",
])
def test_invalid_exact_cas_vetoes_old_on_before_get_and_at_commit(
    tmp_path, provider, kind, index, cas_failure,
):
    storage = Storage(tmp_path / "consent.sqlite3")
    try:
        scope = ProviderRightsScope("identity-a", provider, "credential-a", "account-a")
        source = SourceKey(scope, "edition-a", "resource-a", kind, "branch-a")
        from cifereca.core.legacy_source_consent_metadata import historical_consent_keys
        switch, active, revision_key = historical_consent_keys(scope)[index]
        storage.metadata_set(source_enablement_key(scope), "1")
        storage.metadata_set(switch, active)
        storage.metadata_set(revision_key, "a" * 32)

        # The persisted historical switch is still readable. Only old
        # purchase switches retain compatibility authority; an old loan ON
        # may never stage a one-use signed ACSM GET without explicit CAS.
        assert read_policy(storage, scope).mode == "unset"
        assert bridge.legacy_source_auto_policy(storage, scope, kind) == (
            "on", "a" * 32,
        )
        # Even a fully versioned historical purchase switch is no longer
        # an executable source-wide grant (same rule as retired loan ON).
        assert not bridge.legacy_source_auto_current(storage, source, "a" * 32)
        with storage._lock, storage._connect() as db:
            assert bridge.toggle_state(db, source) == "off"
            assert not bridge.legacy_source_auto_in_transaction(
                db, source, "a" * 32,
            )

        payload = {
            "schema": "cifereca.source-sync-consent/1",
            "scope": [scope.identity_id, scope.provider_id,
                      scope.credential_id, scope.account_id],
            "enabled": True,
            "revision": "b" * 32,
        }
        if cas_failure == "malformed-json":
            invalid = "{"
        elif cas_failure == "wrong-scope":
            payload["scope"][0] = "identity-b"
            invalid = json.dumps(payload)
        else:
            invalid = json.dumps(payload)[:-1] + ',"enabled":false}'
        storage.metadata_set(policy_key(scope), invalid)

        # CAS invalid is *configured*, not absent. Every old ON path must
        # deny without promoting or repairing the persisted policy.
        assert read_policy(storage, scope).mode == "invalid"
        assert bridge.consent_generation(storage, scope) == "invalid"
        assert not bridge.legacy_source_auto_current(storage, source, "a" * 32)
        with storage._lock, storage._connect() as db:
            assert bridge.toggle_state(db, source) == "off"
            assert bridge.consent_generation_in_transaction(db, source) == "invalid"
            assert not bridge.legacy_source_auto_in_transaction(db, source, "a" * 32)
        assert storage.metadata_get(switch) == active
        assert storage.metadata_get(revision_key) == "a" * 32
        assert storage.metadata_get(policy_key(scope)) == invalid
    finally:
        storage.close()
