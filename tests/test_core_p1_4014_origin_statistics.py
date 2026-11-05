"""Compare the persisted Accueil counts with canonical Library evidence."""
from hashlib import sha256
from types import SimpleNamespace

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.library_projection import _verified_provider_origin_coordinates
from cifereca.core.sync_projection import action_statistics, identity_statistics
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register, store, target,
)


def _stats(store, identity):
    service = SimpleNamespace(storage=store, accounts_for_identity=lambda _: [])
    service.action_statistics = lambda *args, **kwargs: action_statistics(service, *args, **kwargs)
    return identity_statistics(service, identity)


def test_six_canonical_acquisitions_count_without_placement_provider(store, tmp_path):
    register(store, "A", "numilog", "card-A")
    for index in range(6):
        edition = f"edition-{index}"
        source, snapshot = new_source(provider="numilog", edition=edition)
        path = target(tmp_path, "A", f"{edition}.pdf")
        payload = path.read_bytes() + edition.encode()
        path.write_bytes(payload)
        publish_completed_acquisition(
            store, library_root=tmp_path / "EBooks", path=path,
            source=source, snapshot=snapshot, final_sha256=sha256(payload).hexdigest(),
            delivery_receipt_ref="synthetic-completed-delivery",
        )
    assert all(row["provider_id"] is None for row in store.placements())
    stats = _stats(store, "A")
    assert stats["sources"] == {"numilog": 6}
    assert stats["local_books"] == 6
    assert stats["local_books_without_provider_origin"] == 0
    assert stats["remote_owned_rights"] == 0


def test_duplicate_placements_accounts_and_providers_preserve_profile_scope(store, tmp_path):
    for provider, credential, account in (
        ("numilog", "card-A", "buyer-A"),
        ("numilog", "second-card", "second-buyer"),
        ("kobo", "kobo-card", "kobo-buyer"),
    ):
        register(store, "A", provider, credential, account=account)
        source, snapshot = new_source(provider=provider, credential=credential, account=account)
        path = target(tmp_path, "A", f"{credential}.pdf")
        result = publish(store, tmp_path / "EBooks", path, source, snapshot)
    store.placement_put({
        "artifact_id": result.artifact_id,
        "identity_id": "B", "provider_id": "numilog",
        "path": target(tmp_path, "B"),
    })
    store.entitlement_put({
        "identity_id": "B", "provider_id": "numilog", "credential_id": "card-B",
        "account_id": "buyer-B", "item_id": "remote-only", "state": "active",
    })
    stats_a = _stats(store, "A")
    assert stats_a["local_placements"] == 3
    assert stats_a["local_books"] == 1
    assert stats_a["sources"] == {"kobo": 1, "numilog": 1}
    assert stats_a["local_books_without_provider_origin"] == 0
    library_origins = _verified_provider_origin_coordinates(store, identity_id="A")
    assert len(library_origins) == 3
    assert {origin[2] for origin in library_origins} == set(stats_a["sources"])

    stats_b = _stats(store, "B")
    assert stats_b["local_books"] == 1
    assert stats_b["sources"] == {}
    assert stats_b["local_books_without_provider_origin"] == 1
    assert stats_b["remote_owned_rights"] == 1


def test_missing_placement_and_unavailable_schema_are_distinct(store, tmp_path):
    register(store, "A", "numilog", "card-A")
    source, snapshot = new_source(provider="numilog")
    path = target(tmp_path, "A")
    publish(store, tmp_path / "EBooks", path, source, snapshot)
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET state='missing'")
    stats = _stats(store, "A")
    assert stats["sources"] == {}
    assert stats["local_books_without_provider_origin"] == 0

    with store._lock, store._connect() as db:
        db.execute("DROP TABLE origin_links")
    stats = _stats(store, "A")
    assert stats["sources"] == {}
    assert stats["local_books_without_provider_origin"] is None
