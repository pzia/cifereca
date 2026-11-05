"""P1 #4520: exact source-placement selection with a global Artifact SHA.

All bytes and SQLite are synthetic and local. A cache entry is NEVER a
physical Placement or derived origin for another identity.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import shutil

import pytest

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
from cifereca.plugin_api import Conflict, TransformRequest
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


def _two_profiles(tmp_path):
    service, original, pid_a = make_service(tmp_path)
    service.storage.identity_put({
        "identity_id": "id2", "label": "Another", "directory_name": "other",
    })
    other = tmp_path / "other" / "Book.epub"
    other.parent.mkdir()
    shutil.copyfile(tmp_path / "library" / "Book.epub", other)
    pid_b = service.storage.placement_put({
        "artifact_id": original.artifact_id, "path": str(other),
        "identity_id": "id2", "provider_id": "second-source",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    service.storage.catalog_entry_put({
        "artifact_id": original.artifact_id, "placement_id": pid_b,
        "title": "Other local copy", "author": "Unknown", "media_format": "epub",
        "identity_id": "id2", "provider_id": "second-source",
        "item_id": "book-1", "local_state": "local",
        "metadata": {"path": str(other)},
    })
    transformer = FakeTransformer()
    service.plugin_manager = FakeManager(transformer=transformer)
    broker = ArtifactExecutionBrokerImpl(service)
    return service, original, (pid_a, pid_b), transformer, broker


def _origins(storage, artifact_id):
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT identity_id,parent_artifact_id,source_reference_id,"
            "metadata_json FROM origin_links "
            "WHERE artifact_id=? AND origin_kind='derived' ORDER BY identity_id",
            (artifact_id,),
        ).fetchall()


def test_same_sha_two_profiles_creates_independent_verified_placements_and_origins(tmp_path):
    service, source, (pid_a, pid_b), transformer, broker = _two_profiles(tmp_path)
    req_a = TransformRequest(
        source, "optimize", identity_id="id1", placement_id=pid_a,
    )
    req_b = replace(req_a, identity_id="id2", placement_id=pid_b)
    try:
        first = broker.execute_transform(req_a, control=Control())
        assert transformer.transform_calls == 1
        assert len([p for p in service.storage.placements()
                    if p["artifact_id"] == first.artifact_id]) == 1

        service.current_identity = NS(identity_id="id2")
        second = broker.execute_transform(req_b, control=Control())
        assert transformer.transform_calls == 2
        assert first.artifact_id == second.artifact_id
        children = [p for p in service.storage.placements()
                    if p["artifact_id"] == first.artifact_id]
        assert len(children) == 2
        assert {x["identity_id"] for x in children} == {"id1", "id2"}
        assert {x["provider_id"] for x in children} == {"cifereca"}
        assert len({x["path"] for x in children}) == 2
        assert all(Path(x["path"]).read_bytes() == b"book-data-optimized"
                   for x in children)
        assert [x["identity_id"] for x in children] == ["id1", "id2"]
        links = _origins(service.storage, first.artifact_id)
        assert len(links) == 2
        assert {i for i, parent, ref, _ in links} == {"id1", "id2"}
        assert all(parent == source.artifact_id and ref is None
                   for _, parent, ref, _ in links)
        # One recipe global, but origin/Placement independently attested.
        assert len(service.storage.artifact_variants(
            source_artifact_id=source.artifact_id,
        )) == 1
        assert service.storage.placement_get(pid_a)["provider_id"] == "local"
        assert service.storage.placement_get(pid_b)["provider_id"] == "second-source"

        again_b = broker.execute_transform(req_b, control=Control())
        assert again_b.artifact_id == first.artifact_id
        service.current_identity = NS(identity_id="id1")
        again_a = broker.execute_transform(req_a, control=Control())
        assert again_a.artifact_id == first.artifact_id
        assert transformer.transform_calls == 2
        assert len(_origins(service.storage, first.artifact_id)) == 2
    finally:
        service.storage.close()


def test_global_sha_without_both_scopes_never_guesses_source(tmp_path):
    service, source, (pid_a, pid_b), transformer, broker = _two_profiles(tmp_path)
    try:
        invalid = (
            (TransformRequest(source, "optimize"), "artifact_source_ambiguous"),
            (TransformRequest(source, "optimize", identity_id="id1"),
             "artifact_source_scope_incomplete"),
            (TransformRequest(source, "optimize", placement_id=pid_a),
             "artifact_source_scope_incomplete"),
            (TransformRequest(source, "optimize", identity_id="id1",
                              placement_id=pid_b), "artifact_source_scope_unverified"),
            (TransformRequest(source, "optimize", identity_id="id2",
                              placement_id=pid_b), "artifact_source_identity_mismatch"),
        )
        for request, code in invalid:
            with pytest.raises(Conflict) as err:
                broker.execute_transform(request, control=Control())
            assert err.value.code == code
        assert transformer.transform_calls == 0
        assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)
        assert not (tmp_path / "other" / "Optimized.epub").exists()
    finally:
        service.storage.close()


def test_multiple_copies_in_one_identity_need_exact_placement_not_just_identity(tmp_path):
    service, source, pid = make_service(tmp_path)
    other = tmp_path / "library" / "Independent.epub"
    shutil.copyfile(tmp_path / "library" / "Book.epub", other)
    pid2 = service.storage.placement_put({
        "artifact_id": source.artifact_id, "path": str(other),
        "identity_id": "id1", "provider_id": "local",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    transformer = FakeTransformer()
    service.plugin_manager = FakeManager(transformer=transformer)
    broker = ArtifactExecutionBrokerImpl(service)
    try:
        with pytest.raises(Conflict) as err:
            broker.execute_transform(
                TransformRequest(source, "optimize"), control=Control(),
            )
        assert err.value.code == "artifact_source_ambiguous"
        out = broker.execute_transform(
            TransformRequest(source, "optimize", identity_id="id1",
                             placement_id=pid2), control=Control(),
        )
        assert out.artifact_id != source.artifact_id
        assert (tmp_path / "library" / "Optimized.epub").is_file()
        assert service.storage.placement_get(pid)["artifact_id"] == source.artifact_id
        assert service.storage.placement_get(pid2)["artifact_id"] == source.artifact_id
    finally:
        service.storage.close()


def test_foreign_cached_sha_without_exact_derived_origin_is_not_reused(tmp_path):
    service, source, (pid_a, pid_b), transformer, broker = _two_profiles(tmp_path)
    req_a = TransformRequest(source, "optimize", identity_id="id1", placement_id=pid_a)
    try:
        child = broker.execute_transform(req_a, control=Control())
        # A copied file in B is not a Cifereca-derived origin for B, even
        # though its SHA equals the globally indexed recipe's artifact.
        copied = tmp_path / "other" / "Outside-optimized.epub"
        shutil.copyfile(tmp_path / "library" / "Optimized.epub", copied)
        fake_pid = service.storage.placement_put({
            "artifact_id": child.artifact_id, "path": str(copied),
            "identity_id": "id2", "provider_id": "manual",
            "source_item_id": "unrelated", "state": "present", "metadata": {},
        })
        service.current_identity = NS(identity_id="id2")
        req_b = replace(req_a, identity_id="id2", placement_id=pid_b)
        result = broker.execute_transform(req_b, control=Control())
        assert result.artifact_id == child.artifact_id
        assert transformer.transform_calls == 2  # no false hit in B
        assert Path(service.storage.placement_get(fake_pid)["path"]).read_bytes() == (
            b"book-data-optimized"
        )
        # The real derived origin and placement are distinct, never rebinding
        # B's unrelated previously imported physical file.
        assert len(_origins(service.storage, child.artifact_id)) == 2
        placements_b = [p for p in service.storage.placements()
                        if p["artifact_id"] == child.artifact_id
                        and p["identity_id"] == "id2"]
        assert len(placements_b) == 2
        assert any(p["provider_id"] == "manual" for p in placements_b)
        assert any(p["provider_id"] == "cifereca" for p in placements_b)
    finally:
        service.storage.close()
