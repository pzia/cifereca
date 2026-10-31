"""#3171: source consent revision must survive the publication SQL boundary."""
import json
import secrets

import pytest

from cifereca.core.source_sync_consent import policy_key
from pathlib import Path
import hashlib
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca.storage import Storage

PAYLOAD = b"%PDF-1.7\\nSynthetic consent test\\n"
SHA = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    try:
        yield storage
    finally:
        storage.close()


def new_source():
    scope = ProviderRightsScope("A", "seller", "card-A", "buyer-A")
    key = SourceKey(scope, "edition", "purchase", "purchase", "purchases")
    observed = ProviderRightObservation(
        "edition", "purchase", "purchase", "active", "provider_response",
        "positive-right-observation", "2026-10-28T08:00:00+00:00", "unknown",
    )
    snapshot = ProviderRightsSnapshot(
        scope, (observed,), status="partial", coverage="partial", branch="purchases",
    )
    return key, snapshot


def register(store, profile, provider, credential):
    store.identity_put({
        "identity_id": profile, "directory_name": profile, "label": profile,
    })
    connection_id = store.provider_connection_put({
        "identity_id": profile, "provider_id": provider,
        "credential_id": credential, "label": "Synthetic account",
    })
    store.provider_account_put({
        "identity_id": profile, "provider_id": provider,
        "credential_id": credential, "connection_id": connection_id,
        "account_id": "buyer-A", "label": "Synthetic borrower",
    })


def target(tmp_path, profile):
    path = tmp_path / "EBooks" / profile / "book.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    return path


def publish(store, root, path, source, snapshot, **kwargs):
    return publish_completed_acquisition(
        store, library_root=root, path=path, source=source,
        snapshot=snapshot, final_sha256=SHA,
        delivery_receipt_ref="synthetic-completed-delivery", **kwargs,
    )


def rows(store):
    with store._lock, store._connect() as db:
        return tuple(db.execute("SELECT count(*) FROM " + name).fetchone()[0]
                     for name in ("artifacts", "placements", "source_references", "origin_links"))


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
