"""P1 #4520: derived EPUB/PDF is a local origin, never a supplier right.

Real synthetic bytes + SQLite. No provider, Adobe, user library or network.
The exact same transaction either publishes all evidence or rolls it back.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl, LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import LibraryMutationRequest, TransformRequest
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


def _setup(tmp_path):
    service, source, source_placement = make_service(tmp_path)
    transformer = FakeTransformer()
    service.plugin_manager = FakeManager(transformer=transformer)
    return service, source, source_placement, transformer, ArtifactExecutionBrokerImpl(service)


def _origins(storage, artifact_id):
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT origin_kind,identity_id,parent_artifact_id,"
            "source_reference_id,evidence_ref,metadata_json "
            "FROM origin_links WHERE artifact_id=?",
            (artifact_id,),
        ).fetchall()


@pytest.mark.parametrize("extension", ["epub"])
def test_single_derived_variant_commits_exact_origin_and_local_placement(tmp_path, extension):
    service, source, source_pid, transformer, broker = _setup(tmp_path)
    try:
        result = broker.execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        assert result.sha256 == hashlib.sha256(b"book-data-optimized").hexdigest()
        assert result.artifact_id != source.artifact_id
        derivative = [
            p for p in service.storage.placements()
            if p["artifact_id"] == result.artifact_id
        ]
        assert len(derivative) == 1
        row = derivative[0]
        assert row["identity_id"] == "id1"
        assert row["provider_id"] == "cifereca"
        assert row["metadata"]["source_placement_id"] == source_pid
        assert row["metadata"]["variant"] is True
        assert Path(row["path"]).read_bytes() == b"book-data-optimized"
        assert service.storage.placement_get(source_pid)["provider_id"] == "local"
        links = _origins(service.storage, result.artifact_id)
        assert len(links) == 1
        kind, identity, parent, supplier_ref, evidence, metadata = links[0]
        assert (kind, identity, parent, supplier_ref) == (
            "derived", "id1", source.artifact_id, None,
        )
        assert evidence.startswith("derived:")
        assert source.sha256 in metadata and result.sha256 in metadata
        assert transformer.transform_calls == 1
        # Local derivative has an INTERNAL catalogue projection, not a
        # copied provider/acquisition SourceReference.
        catalogue = [
            c for c in service.storage.catalog_entries()
            if c["artifact_id"] == result.artifact_id
        ]
        assert len(catalogue) == 1
        assert catalogue[0]["provider_id"] == "cifereca"
        assert catalogue[0]["item_id"] == row["source_item_id"]
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM source_references WHERE identity_id='id1'"
            ).fetchone()[0] == 0

        again = broker.execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        assert again.artifact_id == result.artifact_id
        assert transformer.transform_calls == 1
        assert len(_origins(service.storage, result.artifact_id)) == 1
        assert len([
            p for p in service.storage.placements()
            if p["artifact_id"] == result.artifact_id
        ]) == 1
    finally:
        service.storage.close()


def test_injected_origin_sql_failure_rolls_back_all_variant_tables(tmp_path):
    service, source, original_pid, _transformer, broker = _setup(tmp_path)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "CREATE TRIGGER fail_derived_insert BEFORE INSERT ON origin_links "
            "WHEN NEW.origin_kind='derived' BEGIN "
            "SELECT RAISE(ABORT,'synthetic_derived_sql_failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_derived_sql_failure"):
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert service.storage.artifact_variants(source_artifact_id=source.artifact_id) == []
    assert service.storage.placement_get(original_pid)["artifact_id"] == source.artifact_id
    assert [p for p in service.storage.placements() if p["artifact_id"] != source.artifact_id] == []
    assert [x for x in service.storage.artifacts() if x["artifact_id"] != source.artifact_id] == []
    assert service.storage.catalog_entries()[0]["artifact_id"] == source.artifact_id
    # The published physical inode may remain as an unclaimed orphan after a
    # crash; never claim it present without SQLite evidence or unlink blindly.
    assert (tmp_path / "library" / "Book.epub").read_bytes() == b"book-data"
    with service.storage._lock, service.storage._connect() as db:
        db.execute("DROP TRIGGER fail_derived_insert")


def test_local_delete_keeps_parent_and_immutable_derived_origin_history(tmp_path):
    service, source, source_pid, _transformer, broker = _setup(tmp_path)
    derived = broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    derived_path = next(
        Path(p["path"]) for p in service.storage.placements()
        if p["artifact_id"] == derived.artifact_id
    )
    maintenance = LibraryMaintenanceBrokerImpl(service)
    result = maintenance.run(
        maintenance.plan(LibraryMutationRequest(
            "delete_variant", artifact_id=derived.artifact_id,
        )),
        Control(),
    )
    assert result.ok
    assert not derived_path.exists()
    assert service.storage.placement_get(source_pid)["artifact_id"] == source.artifact_id
    assert (tmp_path / "library" / "Book.epub").read_bytes() == b"book-data"
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id) == []
    assert service.storage.artifact_get(derived.artifact_id) is not None
    assert _origins(service.storage, derived.artifact_id)[0][:4] == (
        "derived", "id1", source.artifact_id, None,
    )
    assert service.storage.artifact_delete_if_unreferenced(derived.artifact_id) is False
    assert service.storage.artifact_delete_if_unreferenced(source.artifact_id) is False
