"""P0 #3719: a legacy primary-account sentinel is never another borrower's grant.

Synthetic SQLite only; no provider network, rights, delivery or files.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.source_auto_consent_bridge import _source_id, toggle_state
from cifereca.core.source_convergence import SourceKey
from cifereca.core.source_sync_consent import policy_key
from cifereca.plugin_api import ProviderRightsScope
from cifereca.storage import Storage


def reference(account_id):
    return SourceKey(
        ProviderRightsScope("reader", "kobo", "card", account_id),
        "edition", "right", "purchase", "owned",
    )


@pytest.mark.parametrize("literal_id", [
    "kobo:card:primary", " kobo:card:primary ", "\t",
])
def test_legacy_primary_fallback_collision_never_grants_literal_borrower(
    tmp_path, literal_id,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        primary = reference("")
        literal = reference(literal_id)
        unrelated = reference("actual-reader")
        padded_unrelated = reference(" actual-reader ")
        sid = _source_id(primary.scope)
        assert sid == _source_id(literal.scope)
        assert sid != _source_id(unrelated.scope)
        assert _source_id(unrelated.scope) == _source_id(padded_unrelated.scope)
        storage.metadata_set("sync.source.materialization.on_start." + sid, "1")
        storage.metadata_set(
            "sync.source.materialization.on_start." + _source_id(unrelated.scope),
            "1",
        )
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            assert toggle_state(db, primary) == "on"
            assert toggle_state(db, literal) == "off"
            assert toggle_state(db, unrelated) == "on"
            assert toggle_state(db, padded_unrelated) == "off"
        # The same legacy metadata remains on disk; the Core cannot
        # reinterpret it as consent for a distinct literal borrower.
        assert storage.metadata_get(
            "sync.source.materialization.on_start." + sid
        ) == "1"
    finally:
        storage.close()


def test_explicit_exact_scope_policy_can_enable_literal_borrower(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        literal = reference("kobo:card:primary")
        primary = reference("")
        sid = _source_id(literal.scope)
        storage.metadata_set("sync.source.materialization.on_start." + sid, "1")
        storage.metadata_set("sync.source.enabled." + sid, "1")
        # Model the output of the explicit CAS policy writer: this is NOT
        # derived from the ambiguous source-id key.
        storage.metadata_set(policy_key(literal.scope), json.dumps({
            "schema": "cifereca.source-sync-consent/1",
            "scope": list((
                literal.scope.identity_id, literal.scope.provider_id,
                literal.scope.credential_id, literal.scope.account_id,
            )),
            "enabled": True,
            "revision": "a" * 32,
        }, sort_keys=True))
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            assert toggle_state(db, literal) == "on"
            # The generic literal account's policy cannot authorize the
            # distinct empty-primary borrower sharing its legacy SID.
            assert toggle_state(db, primary) == "on"
        assert policy_key(literal.scope) != policy_key(primary.scope)
    finally:
        storage.close()
