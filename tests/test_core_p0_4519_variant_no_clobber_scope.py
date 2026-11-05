"""P0 #4519: no-clobber EPUB variant and exact-source transform safety.

Real filesystem + SQLite, synthetic bytes; no network or user ebook.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil

import pytest

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
from cifereca.plugin_api import Conflict, TransformRequest, TransformResult
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


DATA = b"book-data-optimized"
SHA = hashlib.sha256(DATA).hexdigest()


def _broker(tmp_path, transformer=None):
    service, source, original_id = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=transformer or FakeTransformer())
    return service, source, original_id, ArtifactExecutionBrokerImpl(service)


def _assert_no_variant(service, source, original_id, path):
    assert path.read_bytes() == b"book-data"
    assert service.storage.placement_get(original_id)["artifact_id"] == source.artifact_id
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)


def test_two_collisions_never_overwrite_existing_files_or_sqlite(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    original = tmp_path / "library" / "Book.epub"
    initial = tmp_path / "library" / "Optimized.epub"
    fallback = tmp_path / "library" / f"Optimized-{SHA[:8]}.epub"
    initial.write_bytes(b"unrelated first variant")
    fallback.write_bytes(b"valuable unrelated second variant")
    rows_before = tuple((x["placement_id"], x["path"], x["artifact_id"])
                        for x in service.storage.placements())
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_variant_filename_conflict"
    assert initial.read_bytes() == b"unrelated first variant"
    assert fallback.read_bytes() == b"valuable unrelated second variant"
    assert tuple((x["placement_id"], x["path"], x["artifact_id"])
                 for x in service.storage.placements()) == rows_before
    _assert_no_variant(service, source, original_id, original)


def test_primary_collision_exclusive_fallback_preserves_primary(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    primary = tmp_path / "library" / "Optimized.epub"
    fallback = tmp_path / "library" / f"Optimized-{SHA[:8]}.epub"
    primary.write_bytes(b"unrelated primary")
    converted = broker.execute_transform(TransformRequest(source, "optimize"),
                                         control=Control())
    assert converted.sha256 == SHA
    assert primary.read_bytes() == b"unrelated primary"
    assert fallback.read_bytes() == DATA
    assert service.storage.placement_get(original_id) is not None
    assert service.storage.artifact_variants(source_artifact_id=source.artifact_id)


def test_existing_symlink_cannot_be_replaced_or_followed(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    primary = tmp_path / "library" / "Optimized.epub"
    fallback = tmp_path / "library" / f"Optimized-{SHA[:8]}.epub"
    foreign = tmp_path / "independent.epub"
    foreign.write_bytes(b"foreign book must survive")
    primary.symlink_to(foreign)
    fallback.symlink_to(foreign)
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_variant_filename_conflict"
    assert primary.is_symlink() and fallback.is_symlink()
    assert foreign.read_bytes() == b"foreign book must survive"
    _assert_no_variant(service, source, original_id, tmp_path / "library" / "Book.epub")


def test_racing_writer_is_never_clobbered(tmp_path, monkeypatch):
    service, source, original_id, broker = _broker(tmp_path)
    primary = tmp_path / "library" / "Optimized.epub"
    fallback = tmp_path / "library" / f"Optimized-{SHA[:8]}.epub"
    primary.write_bytes(b"first independent")
    actual_link = os.link

    def race(source_name, target_name, *args, **kwargs):
        if target_name == fallback.name:
            fallback.write_bytes(b"second independent inserted at commit")
        return actual_link(source_name, target_name, *args, **kwargs)

    monkeypatch.setattr("cifereca.artifact_runtime.os.link", race)
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_variant_filename_conflict"
    assert primary.read_bytes() == b"first independent"
    assert fallback.read_bytes() == b"second independent inserted at commit"
    _assert_no_variant(service, source, original_id, tmp_path / "library" / "Book.epub")


def test_same_sha_placed_in_two_profiles_refuses_all_source_and_cache_selection(
    tmp_path,
):
    service, source, original_id, broker = _broker(tmp_path)
    other = tmp_path / "other-profile" / "Book.epub"
    other.parent.mkdir()
    shutil.copyfile(tmp_path / "library" / "Book.epub", other)
    service.storage.placement_put({
        "artifact_id": source.artifact_id, "path": str(other),
        "identity_id": "another-profile", "provider_id": "local",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_source_ambiguous"
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert not (other.parent / "Optimized.epub").exists()
    assert other.read_bytes() == b"book-data"
    _assert_no_variant(service, source, original_id, tmp_path / "library" / "Book.epub")


def test_two_paths_of_one_profile_still_need_exact_placement(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    duplicate = tmp_path / "library" / "Copy.epub"
    shutil.copyfile(tmp_path / "library" / "Book.epub", duplicate)
    service.storage.placement_put({
        "artifact_id": source.artifact_id, "path": str(duplicate),
        "identity_id": "id1", "provider_id": "local",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    with pytest.raises(Conflict) as caught:
        broker.input_lease(source)
    assert caught.value.code == "artifact_source_ambiguous"
    assert duplicate.read_bytes() == b"book-data"
    _assert_no_variant(service, source, original_id, tmp_path / "library" / "Book.epub")


def test_changed_source_bytes_during_transform_refuses_publication(tmp_path):
    class RewriteOriginal(FakeTransformer):
        def transform(self, request, source, destination, control):
            result = super().transform(request, source, destination, control)
            source.path.write_bytes(b"unrelated swapped source")
            return result
    service, source, original_id, broker = _broker(tmp_path, RewriteOriginal())
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_source_changed"
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert service.storage.placement_get(original_id)["artifact_id"] == source.artifact_id
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)


def test_swapped_parent_directory_is_detected_even_if_bytes_are_same(tmp_path):
    class SwapFolder(FakeTransformer):
        def transform(self, request, source, destination, control):
            result = super().transform(request, source, destination, control)
            original = tmp_path / "library"
            old_inode = tmp_path / "moved-library"
            original.rename(old_inode)
            original.mkdir()
            (original / "Book.epub").write_bytes(b"book-data")
            return result
    service, source, original_id, broker = _broker(tmp_path, SwapFolder())
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_source_changed"
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert not (tmp_path / "moved-library" / "Optimized.epub").exists()
    assert (tmp_path / "moved-library" / "Book.epub").read_bytes() == b"book-data"
    assert service.storage.placement_get(original_id) is not None


def test_cancellation_after_transform_creates_no_final_variant(tmp_path):
    class Cancelled(Control):
        stop = False
        def check_cancelled(self):
            if self.stop:
                raise RuntimeError("synthetic cancelled")
    class Interrupt(FakeTransformer):
        def transform(self, request, source, destination, control):
            result = super().transform(request, source, destination, control)
            control.stop = True
            return result
    service, source, original_id, broker = _broker(tmp_path, Interrupt())
    with pytest.raises(RuntimeError, match="synthetic cancelled"):
        broker.execute_transform(TransformRequest(source, "optimize"),
                                 control=Cancelled())
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    _assert_no_variant(service, source, original_id, tmp_path / "library" / "Book.epub")
