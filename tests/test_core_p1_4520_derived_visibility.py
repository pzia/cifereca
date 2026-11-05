"""#4520: read-only variant projection is per local derived OriginLink.

A copied SHA, a global recipe and a manually imported Placement never prove a
derivative in the viewing identity. Files and SQLite are synthetic only.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import shutil

from cifereca.artifact_runtime import (
    ArtifactExecutionBrokerImpl, LibraryReadBrokerImpl,
)
from cifereca.plugin_api import TransformRequest
from tests.test_core041_artifact_authority import Control
from tests.test_core_p1_4520_exact_placement_transform import _two_profiles


def _request(source, identity, placement):
    return TransformRequest(
        source, "optimize", identity_id=identity, placement_id=placement,
    )


def test_variant_listing_and_book_view_do_not_cross_two_profiles(tmp_path):
    service, source, (pid_a, pid_b), transformer, broker = _two_profiles(tmp_path)
    read = LibraryReadBrokerImpl(service)
    try:
        result_a = broker.execute_transform(
            _request(source, "id1", pid_a), control=Control(),
        )
        assert [v.artifact_id for v in broker.variants(source)] == (
            [result_a.artifact_id]
        )
        view_a = read.book("book-1", identity_id="id1")
        assert [v.artifact_id for v in view_a.variants] == [result_a.artifact_id]
        service.current_identity = NS(identity_id="id2")
        assert broker.variants(source) == ()
        assert broker.variants(
            replace(source, identity_id="id1", placement_id=pid_a)
        ) == ()
        view_b = read.book("book-1", identity_id="id2")
        assert view_b is not None and view_b.variants == ()
        assert {a.artifact_id for a in view_b.artifacts} == {source.artifact_id}

        # B's pre-existing unrelated filename forces a different output
        # basename; visibility must expose its own physical Placement name.
        occupied = tmp_path / "other" / "Optimized.epub"
        occupied.write_bytes(b"independent file")
        result_b = broker.execute_transform(
            _request(source, "id2", pid_b), control=Control(),
        )
        assert result_b.artifact_id == result_a.artifact_id
        variants_b = broker.variants(
            replace(source, identity_id="id2", placement_id=pid_b)
        )
        assert len(variants_b) == 1
        assert variants_b[0].filename.startswith("Optimized-")
        assert occupied.read_bytes() == b"independent file"
        assert [v.artifact_id for v in read.book(
            "book-1", identity_id="id2"
        ).variants] == [result_b.artifact_id]
        service.current_identity = NS(identity_id="id1")
        assert broker.variants(
            replace(source, identity_id="id1", placement_id=pid_a)
        )[0].filename == "Optimized.epub"
        assert transformer.transform_calls == 2
    finally:
        service.storage.close()


def test_manual_copy_of_sha_without_derived_origin_is_not_a_variant(tmp_path):
    service, source, (pid_a, pid_b), _transformer, broker = _two_profiles(tmp_path)
    read = LibraryReadBrokerImpl(service)
    try:
        child = broker.execute_transform(
            _request(source, "id1", pid_a), control=Control(),
        )
        output = next(
            Path(p["path"]) for p in service.storage.placements()
            if p["artifact_id"] == child.artifact_id
        )
        copied = tmp_path / "other" / "Manual.epub"
        shutil.copyfile(output, copied)
        service.storage.placement_put({
            "artifact_id": child.artifact_id, "path": str(copied),
            "identity_id": "id2", "provider_id": "manual",
            "source_item_id": "book-1", "state": "present",
            "metadata": {},
        })
        service.current_identity = NS(identity_id="id2")
        assert broker.variants(source) == ()
        view = read.book("book-1", identity_id="id2")
        assert view is not None and view.variants == ()
        assert {a.artifact_id for a in view.artifacts} == {source.artifact_id}
        assert read.for_artifact(child.artifact_id, identity_id="id2") is None
        assert copied.read_bytes() == b"book-data-optimized"
        assert service.storage.placement_get(pid_b)["artifact_id"] == (
            source.artifact_id
        )
    finally:
        service.storage.close()


def test_two_originals_of_one_identity_respect_selected_parent_placement(tmp_path):
    service, source, (pid_a, _pid_b), _transformer, broker = _two_profiles(tmp_path)
    try:
        child = broker.execute_transform(
            _request(source, "id1", pid_a), control=Control(),
        )
        sibling = tmp_path / "library" / "Independent.epub"
        shutil.copyfile(tmp_path / "library" / "Book.epub", sibling)
        sibling_id = service.storage.placement_put({
            "artifact_id": source.artifact_id, "path": str(sibling),
            "identity_id": "id1", "provider_id": "local",
            "source_item_id": "book-1", "state": "present", "metadata": {},
        })
        assert broker.variants(
            replace(source, identity_id="id1", placement_id=sibling_id)
        ) == ()
        original = broker.variants(
            replace(source, identity_id="id1", placement_id=pid_a)
        )
        assert [v.artifact_id for v in original] == [child.artifact_id]
    finally:
        service.storage.close()


def test_retired_origin_does_not_leave_a_false_visible_variant(tmp_path):
    service, source, (pid_a, _pid_b), _transformer, broker = _two_profiles(tmp_path)
    read = LibraryReadBrokerImpl(service)
    try:
        child = broker.execute_transform(
            _request(source, "id1", pid_a), control=Control(),
        )
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "DELETE FROM origin_links WHERE artifact_id=? "
                "AND origin_kind='derived'",
                (child.artifact_id,),
            )
        assert broker.variants(source) == ()
        book = read.book("book-1", identity_id="id1")
        # The physical child remains recorded as a local catalogue item;
        # it is not a *verified* variant of the parent anymore.
        assert book.variants == ()
        assert read.for_artifact(child.artifact_id, identity_id="id1") is None
    finally:
        service.storage.close()
