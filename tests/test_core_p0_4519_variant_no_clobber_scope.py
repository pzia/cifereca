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


def test_missing_sql_placement_at_fallback_does_not_claim_other_profile(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    primary = tmp_path / "library" / "Optimized.epub"
    fallback = tmp_path / "library" / f"Optimized-{SHA[:8]}.epub"
    primary.write_bytes(b"unrelated existing primary")
    foreign_id = service.storage.placement_put({
        "artifact_id": source.artifact_id,
        "path": str(fallback),
        "identity_id": "other-profile",
        "provider_id": "other-provider",
        "source_item_id": "other-edition",
        "state": "missing",
        "metadata": {},
    })
    old_placement = service.storage.placement_get(foreign_id)
    assert not fallback.exists()
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_variant_filename_conflict"
    assert service.storage.placement_get(foreign_id) == old_placement
    assert not fallback.exists()
    assert primary.read_bytes() == b"unrelated existing primary"
    _assert_no_variant(service, source, original_id, tmp_path / "library" / "Book.epub")


def test_same_bytes_at_hash_fallback_are_never_overwritten(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    primary = tmp_path / "library" / "Optimized.epub"
    fallback = tmp_path / "library" / f"Optimized-{SHA[:8]}.epub"
    primary.write_bytes(b"another title")
    fallback.write_bytes(DATA)
    old_stat = fallback.stat()
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_variant_filename_conflict"
    assert fallback.read_bytes() == DATA
    assert (fallback.stat().st_dev, fallback.stat().st_ino) == (
        old_stat.st_dev, old_stat.st_ino
    )
    _assert_no_variant(service, source, original_id, tmp_path / "library" / "Book.epub")


def test_source_symlink_is_rejected_without_touching_destination(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    original = tmp_path / "library" / "Book.epub"
    other = tmp_path / "unrelated-source.epub"
    other.write_bytes(b"book-data")
    original.unlink()
    original.symlink_to(other)
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_source_unverified"
    assert other.read_bytes() == b"book-data"
    assert original.is_symlink()
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)


def test_foreign_only_cached_variant_does_not_satisfy_other_profile(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    first = broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    a_original = tmp_path / "library" / "Book.epub"
    a_variant = tmp_path / "library" / "Optimized.epub"
    assert a_variant.read_bytes() == DATA
    b_original = tmp_path / "profile-b" / "Book.epub"
    b_original.parent.mkdir()
    service.storage.identity_put({
        "identity_id": "id2", "label": "id2", "directory_name": "profile-b",
    })
    shutil.copy2(a_original, b_original)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE placements SET path=?,identity_id='id2' WHERE placement_id=?",
            (str(b_original), original_id),
        )
    service.current_identity.identity_id = "id2"
    second = broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert second.sha256 == first.sha256
    assert (b_original.parent / "Optimized.epub").read_bytes() == DATA
    assert a_variant.read_bytes() == DATA
    assert a_original.read_bytes() == b"book-data"
    assert service.storage.placement_get(original_id)["identity_id"] == "id2"


def test_sole_present_source_outside_registered_profile_is_refused(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    other = tmp_path / "unowned" / "Book.epub"
    other.parent.mkdir()
    shutil.copy2(tmp_path / "library" / "Book.epub", other)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE placements SET path=? WHERE placement_id=?",
            (str(other), original_id),
        )
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_source_scope_unverified"
    assert other.read_bytes() == b"book-data"
    assert not (other.parent / "Optimized.epub").exists()
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)


def test_foreign_profile_path_of_same_sha_never_counts_as_owner(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    foreign = tmp_path / "profile-b" / "Book.epub"
    foreign.parent.mkdir()
    service.storage.identity_put({
        "identity_id": "id2", "label": "id2", "directory_name": "profile-b",
    })
    shutil.copy2(tmp_path / "library" / "Book.epub", foreign)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE placements SET path=? WHERE placement_id=?",
            (str(foreign), original_id),
        )
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_source_scope_unverified"
    assert foreign.read_bytes() == b"book-data"
    assert not (foreign.parent / "Optimized.epub").exists()


def test_swapped_staging_parent_cannot_unlink_foreign_file_on_abort(tmp_path):
    foreign = tmp_path / "foreign-staging"
    foreign.mkdir()

    class SwapStaging(FakeTransformer):
        def transform(self, request, source, destination, control):
            destination.path.write_bytes(DATA)
            victim = foreign / destination.path.name
            victim.write_bytes(b"foreign file of another profile")
            original_stage = destination.path.parent
            original_stage.rename(tmp_path / "old-private-staging")
            original_stage.symlink_to(foreign, target_is_directory=True)
            raise RuntimeError("synthetic transformer failure after swap")

    service, source, original_id, broker = _broker(tmp_path, SwapStaging())
    with pytest.raises(RuntimeError, match="synthetic transformer failure"):
        broker.execute_transform(TransformRequest(source, "optimize"),
                                 control=Control())
    victim, = tuple(foreign.iterdir())
    assert victim.read_bytes() == b"foreign file of another profile"
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)


def test_swapped_staging_parent_cannot_publish_or_unlink_foreign_bytes(tmp_path):
    foreign = tmp_path / "foreign-staging"
    foreign.mkdir()

    class SwapThenSuccess(FakeTransformer):
        def transform(self, request, source, destination, control):
            result = super().transform(request, source, destination, control)
            victim = foreign / destination.path.name
            victim.write_bytes(DATA)
            original_stage = destination.path.parent
            original_stage.rename(tmp_path / "old-private-staging")
            original_stage.symlink_to(foreign, target_is_directory=True)
            return result

    service, source, original_id, broker = _broker(tmp_path, SwapThenSuccess())
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"),
                                 control=Control())
    assert caught.value.code in {
        "artifact_variant_directory_changed", "artifact_variant_publish_failed",
    }
    victim, = tuple(foreign.iterdir())
    assert victim.read_bytes() == DATA
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)


@pytest.mark.parametrize("fail_transform", [True, False])
def test_swapped_intermediate_staging_root_cannot_escape_profile(
    tmp_path, fail_transform,
):
    class SwapRoot(FakeTransformer):
        def transform(self, request, source, destination, control):
            result = super().transform(request, source, destination, control)
            original_root = destination.path.parent.parent
            moved_root = tmp_path / "relocated-staging-root"
            original_root.rename(moved_root)
            original_root.symlink_to(moved_root, target_is_directory=True)
            if fail_transform:
                raise RuntimeError("synthetic stage root swap")
            return result
    service, source, original_id, broker = _broker(tmp_path, SwapRoot())
    if fail_transform:
        with pytest.raises(RuntimeError, match="synthetic stage root swap"):
            broker.execute_transform(TransformRequest(source, "optimize"),
                                     control=Control())
    else:
        with pytest.raises(Conflict) as caught:
            broker.execute_transform(TransformRequest(source, "optimize"),
                                     control=Control())
        assert caught.value.code == "artifact_variant_directory_changed"
    relocated = tuple((tmp_path / "relocated-staging-root" / "variants").iterdir())
    assert len(relocated) == 1 and relocated[0].read_bytes() == DATA
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)
    assert service.storage.placement_get(original_id) is not None


def test_active_other_profile_cannot_convert_unique_foreign_sha(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    # Artifact is unique and verified under id1, but the user is now in id2.
    service.current_identity.identity_id = "id2"
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_source_identity_mismatch"
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)
    assert service.storage.placement_get(original_id)["identity_id"] == "id1"


def test_unknown_active_identity_never_selects_first_sha_placement(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)
    service.current_identity = None
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"), control=Control())
    assert caught.value.code == "artifact_source_identity_mismatch"
    assert not (tmp_path / "library" / "Optimized.epub").exists()


def test_active_identity_change_during_transform_blocks_commit(tmp_path):
    service, source, original_id, broker = _broker(tmp_path)

    class ChangeSelectedProfile(FakeTransformer):
        def transform(self, request, source_lease, destination, control):
            result = super().transform(request, source_lease, destination, control)
            service.current_identity.identity_id = "profile-b"
            return result

    service.plugin_manager = FakeManager(transformer=ChangeSelectedProfile())
    with pytest.raises(Conflict) as caught:
        broker.execute_transform(TransformRequest(source, "optimize"),
                                 control=Control())
    assert caught.value.code == "artifact_source_identity_mismatch"
    assert (tmp_path / "library" / "Book.epub").read_bytes() == b"book-data"
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert service.storage.placement_get(original_id) is not None
    assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)
