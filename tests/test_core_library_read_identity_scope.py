from __future__ import annotations

import hashlib
from types import SimpleNamespace

from cifereca.artifact_runtime import LibraryReadBrokerImpl
from cifereca.plugin_api import LibraryReadBroker
from tests.test_core041_artifact_authority import make_service


def _library(tmp_path):
    service, source, alice_id = make_service(tmp_path)
    service.identities = [
        SimpleNamespace(identity_id="id1"),
        SimpleNamespace(identity_id="id2"),
    ]
    other = tmp_path / "bob" / "Private Bob.epub"
    other.parent.mkdir(parents=True)
    other.write_bytes((tmp_path / "library" / "Book.epub").read_bytes())
    bob_id = service.storage.placement_put({
        "artifact_id": source.artifact_id, "path": str(other),
        "identity_id": "id2", "provider_id": "local",
        "source_item_id": "book-1", "state": "present",
        "metadata": {"storage_target_id": "bob-storage"},
    })
    service.storage.catalog_entry_put({
        "artifact_id": source.artifact_id, "placement_id": bob_id,
        "title": "Livre partagé", "author": "Auteur",
        "media_format": "epub", "identity_id": "id2",
        "provider_id": "local", "item_id": "book-1",
        "local_state": "local", "metadata": {"path": str(other)},
    })
    for identity, credential in (("id1", "vault-a"), ("id2", "vault-b")):
        service.storage.entitlement_put({
            "provider_id": "local", "credential_id": credential,
            "identity_id": identity, "item_id": "book-1",
            "entitlement_type": "owned", "state": "active",
            "metadata": {"title": "Livre partagé"},
        })
    return service, source, alice_id, bob_id


def test_scoped_read_broker_isolates_placements_and_entitlements(tmp_path):
    service, source, alice_id, bob_id = _library(tmp_path)
    broker = LibraryReadBrokerImpl(service)
    assert isinstance(broker, LibraryReadBroker)

    alice = broker.book("book-1", identity_id="id1")
    bob = broker.book("book-1", identity_id="id2")
    assert alice is not None and bob is not None
    assert [p.placement_id for p in alice.placements] == [alice_id]
    assert [p.placement_id for p in bob.placements] == [bob_id]
    assert alice.artifacts[0].artifact_id == bob.artifacts[0].artifact_id == source.artifact_id
    assert alice.artifacts[0].filename == "Book.epub"
    assert bob.artifacts[0].filename == "Private Bob.epub"
    assert {p.identity_id for p in alice.possessions} == {"id1"}
    assert {p.identity_id for p in bob.possessions} == {"id2"}
    assert {p.facts.get("credential_id") for p in alice.possessions if p.kind == "owned"} == {"vault-a"}
    assert {p.facts.get("credential_id") for p in bob.possessions if p.kind == "owned"} == {"vault-b"}
    assert broker.for_artifact(source.artifact_id, identity_id="id1").placements == alice.placements
    assert broker.for_artifact(source.artifact_id, identity_id="id2").placements == bob.placements
    assert broker.artifact(source.artifact_id, identity_id="id1").filename == "Book.epub"
    assert broker.artifact(source.artifact_id, identity_id="id2").filename == "Private Bob.epub"
    assert broker.preferred_artifact("book-1", identity_id="id2").filename == "Private Bob.epub"

    # Existing global read contract remains available to maintenance workflows.
    unscoped = broker.book("book-1")
    assert {p.identity_id for p in unscoped.placements} == {"id1", "id2"}


def test_scoped_read_returns_no_cross_identity_artifact(tmp_path):
    service, source, _, _ = _library(tmp_path)
    broker = LibraryReadBrokerImpl(service)
    assert broker.book("book-1", identity_id="unrelated") is None
    assert broker.for_artifact(source.artifact_id, identity_id="unrelated") is None
    assert broker.artifact(source.artifact_id, identity_id="unrelated") is None
    assert broker.preferred_artifact("book-1", identity_id="unrelated") is None


def test_scoped_variants_do_not_expose_files_in_another_identity(tmp_path):
    service, source, _, _ = _library(tmp_path)
    output = tmp_path / "bob" / "Bob optimized.epub"
    output.write_bytes(b"optimized for Bob")
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    variant_id = f"sha256:{sha}"
    service.storage.artifact_put({
        "artifact_id": variant_id, "sha256": sha, "size": output.stat().st_size,
        "media_format": "epub", "mime_type": "application/epub+zip", "metadata": {},
    })
    service.storage.placement_put({
        "artifact_id": variant_id, "path": str(output), "identity_id": "id2",
        "provider_id": "local", "source_item_id": "book-1",
        "state": "present", "metadata": {},
    })
    service.storage.artifact_variant_put({
        "artifact_id": variant_id, "source_artifact_id": source.artifact_id,
        "source_sha256": source.sha256, "transformer_plugin_id": "optimizer.test",
        "variant_kind": "optimized", "options_fingerprint": "bob",
    })

    broker = LibraryReadBrokerImpl(service)
    alice = broker.book("book-1", identity_id="id1")
    bob = broker.book("book-1", identity_id="id2")
    assert alice is not None and bob is not None
    assert variant_id not in {artifact.artifact_id for artifact in alice.artifacts}
    assert variant_id not in {variant.artifact_id for variant in alice.variants}
    assert variant_id not in {placement.artifact_id for placement in alice.placements}
    assert variant_id in {artifact.artifact_id for artifact in bob.artifacts}
    assert [variant.filename for variant in bob.variants] == ["Bob optimized.epub"]
    assert broker.artifact(variant_id, identity_id="id1") is None
    assert broker.artifact(variant_id, identity_id="id2").filename == "Bob optimized.epub"
    # The source hash is shared, but Bob's derived variant is not Alice's.
    assert broker.book(variant_id, identity_id="id1") is None
    assert broker.for_artifact(variant_id, identity_id="id1") is None
    assert broker.book(variant_id, identity_id="id2") is not None
    assert broker.for_artifact(variant_id, identity_id="id2").variants[0].artifact_id == variant_id
