"""#3196 D1: canonical B stays linked after its 0.7 archive retires.

Synthetic provider rights and PDF bytes; no networking, ADEPT or GTK.
The unlinked count is advisory, not a grant of Kobo rights or a GET.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core.kobo_backup import kobo_unlinked_local_count
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, store, target,
)


def _service(storage, root, identity="A"):
    return SimpleNamespace(
        storage=storage,
        identities=[SimpleNamespace(identity_id=identity, directory_name=identity)],
        library_dir=lambda: root,
    )


def _catalog(storage, path, *, provider="kobo", placement=None, artifact=None):
    storage.catalog_entry_put({
        "catalog_entry_id": "local-candidate",
        "placement_id": placement, "artifact_id": artifact,
        "identity_id": "A", "provider_id": provider,
        "item_id": "book", "title": "Local synthetic",
        "local_state": "local", "metadata": {"path": str(path)},
    })


@pytest.mark.parametrize("state", [
    "canonical", "changed_bytes", "missing_origin", "disabled_account",
    "unattested_source", "foreign_provider",
])
def test_only_exact_current_verified_kobo_b_is_not_unlinked(
    store, tmp_path, state,
):
    provider = "other-supplier" if state == "foreign_provider" else "kobo"
    register(store, "A", provider, "card-a", account="buyer")
    source, snapshot = new_source(
        "A", provider=provider, credential="card-a", account="buyer",
        edition="book", right="book", branch="personal_manifest",
    )
    root = tmp_path / "EBooks"
    file = target(tmp_path, "A")
    published = publish(store, root, file, source, snapshot)
    _catalog(
        store, file, provider="kobo",
        placement=published.placement_id, artifact=published.artifact_id,
    )
    service = _service(store, root)
    assert store.backup_list("kobo") == []
    if state == "changed_bytes":
        file.write_bytes(BYTES[:-1] + b"Z")
    elif state == "missing_origin":
        with store._lock, store._connect() as db:
            db.execute("DELETE FROM origin_links")
    elif state == "disabled_account":
        with store._lock, store._connect() as db:
            db.execute("UPDATE provider_connections SET enabled=0")
    elif state == "unattested_source":
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE source_references SET evidence_kind='historical_receipt'"
            )
    expected = 0 if state == "canonical" else 1
    assert kobo_unlinked_local_count(service, "A") == expected
    assert file.is_file()


def test_legacy_kobo_path_stays_known_without_canonical_source(store, tmp_path):
    register(store, "A", "kobo", "card-a", account="buyer")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    store.backup_put({
        "provider_id": "kobo", "credential_id": "card-a",
        "identity_id": "A", "account_id": "buyer",
        "item_id": "book", "title": "Legacy", "status": "complete",
        "local_path": str(path),
    })
    _catalog(store, path)
    assert kobo_unlinked_local_count(_service(store, root), "A") == 0


def test_other_profile_kobo_origin_does_not_attest_this_profiles_bytes(
    store, tmp_path,
):
    register(store, "B", "kobo", "card-b", account="buyer")
    source, snapshot = new_source(
        "B", provider="kobo", credential="card-b", account="buyer",
        edition="book", right="book", branch="personal_manifest",
    )
    root = tmp_path / "EBooks"
    other = target(tmp_path, "B")
    publish(store, root, other, source, snapshot)
    store.identity_put({"identity_id": "A", "directory_name": "A", "label": "A"})
    file = target(tmp_path, "A")
    _catalog(store, file)
    assert kobo_unlinked_local_count(_service(store, root), "A") == 1
