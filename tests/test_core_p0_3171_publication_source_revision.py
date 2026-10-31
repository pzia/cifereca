"""#3171: source consent revision must survive the publication SQL boundary."""
import json
import secrets

import pytest

from cifereca.core.source_sync_consent import policy_key
from test_core_p0_3171_atomic_publication_v14 import (
    new_source, register, target, publish, rows,
)


def _policy(store, scope, enabled):
    revision = secrets.token_hex(16)
    payload = json.dumps({
        "schema": "cifereca.source-sync-consent/1",
        "scope": [scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id],
        "enabled": enabled,
        "revision": revision,
    })
    with store._lock, store._connect() as db:
        db.execute("INSERT INTO metadata(key,value) VALUES (?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (policy_key(scope), payload))
    return revision


@pytest.mark.parametrize("transition", ("off", "off_on"))
def test_revocation_or_aba_rejects_publication(store, tmp_path, transition):
    key, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    first_revision = _policy(store, key.scope, True)
    _policy(store, key.scope, False)
    if transition == "off_on":
        _policy(store, key.scope, True)
    with pytest.raises(ValueError, match="canonical_source_policy_revoked"):
        publish(store, tmp_path / "EBooks", path, key, snapshot,
                expected_source_policy_revision=first_revision)
    assert rows(store) == (0, 0, 0, 0)


def test_matching_on_generation_can_publish(store, tmp_path):
    key, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    revision = _policy(store, key.scope, True)
    result = publish(store, tmp_path / "EBooks", path, key, snapshot,
                     expected_source_policy_revision=revision)
    assert result.changed
    assert rows(store) == (1, 1, 1, 1)
