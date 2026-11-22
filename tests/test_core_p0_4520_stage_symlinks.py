"""#4520: local symlink observations; never follow or mutate link targets."""
from pathlib import Path
import os

import pytest

from cifereca.core import derived_variant_stage_symlinks as review
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _review(db, root, **overrides):
    options = dict(library_root=root, identity_id="id1", explicitly_requested=True)
    options.update(overrides)
    return review.review_stage_symlinks(db, **options)


def test_stage_aliases_and_unresolved_links_are_separate(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        stage = Path(intent.staged_path)
        profile = Path(intent.parent_path).parent
        (profile / "absolute-link").symlink_to(stage)
        (profile / "relative-link").symlink_to(os.path.relpath(stage, profile))
        (profile / "directory-link").symlink_to(stage.parent, target_is_directory=True)
        (profile / "chain").symlink_to("absolute-link")
        (profile / "dangling").symlink_to("/nonexistent/synthetic")
        (profile / "cycle").symlink_to("cycle")
        result = _review(db, tmp_path)
        assert result["status"] == "observed"
        assert result["lexical_stage_aliases"] == 3
        assert result["unresolved_symlinks"] == 3
        assert not result["external_dependencies_verified"]
        assert not result["retention_authorized"]
        assert str(tmp_path) not in repr(result)
        assert stage.samefile(intent.candidate_path)
    finally:
        db.close()


def test_budget_and_scope_do_not_claim_clear(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        assert _review(db, tmp_path)["status"] == "no_committed_receipts"
        _publish_reserved(db, intent, reservation)
        assert _review(db, tmp_path, explicitly_requested=False)["status"] == "unavailable"
        assert _review(db, tmp_path, identity_id="foreign")["status"] == "unavailable"
        monkeypatch.setattr(review, "_MAX_ENTRIES", 1)
        result = _review(db, tmp_path)
        assert result["status"] == "incomplete"
        assert not result["external_dependencies_verified"]
    finally:
        db.close()


def test_link_added_between_walks_discards_observation(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        original = review._walk
        calls = 0

        def changed(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                (Path(intent.parent_path).parent / "late-link").symlink_to(intent.staged_path)
            return result

        monkeypatch.setattr(review, "_walk", changed)
        result = _review(db, tmp_path)
        assert result["status"] == "evidence_changed"
        assert result["entries_observed"] == 0
    finally:
        db.close()


def test_symlink_profile_never_traversed(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        directory, _ = review._snapshot(db, "id1")
        profile = tmp_path / directory
        moved = tmp_path / "moved-profile"
        profile.rename(moved)
        profile.symlink_to(moved, target_is_directory=True)
        assert _review(db, tmp_path)["status"] == "unavailable"
        assert profile.is_symlink()
    finally:
        db.close()


def test_sibling_and_unregistered_directories_are_not_excluded(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        stage = Path(intent.staged_path)
        sibling = tmp_path / "sibling-profile"
        sibling.mkdir()
        other = tmp_path / "unregistered"
        other.mkdir()
        (sibling / "relative").symlink_to(os.path.relpath(stage, sibling))
        (other / "directory").symlink_to(stage.parent, target_is_directory=True)
        (other / "unresolved").symlink_to("/nonexistent/synthetic")
        result = _review(db, tmp_path)
        assert result["status"] == "observed"
        assert result["lexical_stage_aliases"] == 2
        assert result["unresolved_symlinks"] == 1
        assert not result["retention_authorized"]
        assert not result["quota_refund_authorized"]
        assert not result["external_dependencies_verified"]
        assert str(tmp_path) not in repr(result)
        assert stage.samefile(intent.candidate_path)
    finally:
        db.close()


def test_sibling_alias_added_after_first_walk_invalidates_report(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        sibling = tmp_path / "sibling-profile"
        sibling.mkdir()
        original = review._walk
        calls = 0

        def changed(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                (sibling / "late").symlink_to(intent.staged_path)
            return result

        monkeypatch.setattr(review, "_walk", changed)
        result = _review(db, tmp_path)
        assert result["status"] == "evidence_changed"
        assert result["entries_observed"] == 0
        assert not result["retention_authorized"]
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_same_device_submount_refuses_clear_symlink_observation(tmp_path, monkeypatch):
    from cifereca.core import derived_variant_private_region as region
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        other = tmp_path / "same-device-mount"
        other.mkdir()
        before = Path(intent.staged_path).read_bytes()
        real = region._mount_id
        inode = other.stat().st_ino
        # Synthetic md-independent mount identity with actual same-device
        # directories; no privileged mount or external file access.
        def mount(fd):
            value = real(fd)
            return value + 1 if os.fstat(fd).st_ino == inode else value

        monkeypatch.setattr(region, "_mount_id", mount)
        report = _review(db, tmp_path)
        assert report["status"] == "incomplete"
        assert report["entries_observed"] == 0
        assert not report["external_dependencies_verified"]
        assert not report["retention_authorized"]
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        assert Path(intent.staged_path).read_bytes() == before
    finally:
        db.close()
