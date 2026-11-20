"""#5225: direct loan staging must never clobber older valid library bytes."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from cifereca.core.loan_direct_publication import (
    DirectLoanPublicationRefused,
    publish_direct_loan_candidate as publish,
)


def _stage(tmp_path, name="book.epub", data=b"synthetic provider bytes"):
    stage = tmp_path / "private-work"
    stage.mkdir(exist_ok=True)
    candidate = stage / name
    candidate.write_bytes(data)
    return candidate, stage, tmp_path / "identity" / "paris"


def test_new_direct_loan_publishes_without_stage_or_prior_mutation(tmp_path):
    staged, work, library = _stage(tmp_path)
    result = publish(staged, work_dir=work, library_target_dir=library)
    assert result == library / "book.epub"
    assert result.read_bytes() == b"synthetic provider bytes"
    assert not staged.exists()
    assert not list(library.glob(".loan-publish-*"))


def test_name_collision_preserves_prior_loan_and_uses_distinct_name(tmp_path):
    staged, work, library = _stage(tmp_path, data=b"NEW PROVIDER BYTES")
    library.mkdir(parents=True)
    previous = library / staged.name
    previous.write_bytes(b"OLD VALID LOAN")
    result = publish(staged, work_dir=work, library_target_dir=library)
    assert result != previous
    assert result.parent == library
    assert result.name.startswith("book - ")
    assert result.suffix == ".epub"
    assert previous.read_bytes() == b"OLD VALID LOAN"
    assert result.read_bytes() == b"NEW PROVIDER BYTES"
    assert not staged.exists()


def test_two_concurrent_titles_do_not_overwrite_existing_path(tmp_path):
    staged, work, library = _stage(tmp_path, data=b"loan A")
    first = publish(staged, work_dir=work, library_target_dir=library)
    staged.write_bytes(b"loan B")
    second = publish(staged, work_dir=work, library_target_dir=library)
    assert first != second
    assert first.read_bytes() == b"loan A"
    assert second.read_bytes() == b"loan B"


def test_stage_symlink_and_foreign_stage_are_denied_without_reading(tmp_path):
    staged, work, library = _stage(tmp_path)
    external = tmp_path / "secret.epub"
    external.write_bytes(b"PRIVATE DATA")
    staged.unlink()
    staged.symlink_to(external)
    with pytest.raises(DirectLoanPublicationRefused, match="loan_direct_stage_scope_invalid"):
        publish(staged, work_dir=work, library_target_dir=library)
    assert staged.is_symlink() and external.read_bytes() == b"PRIVATE DATA"
    assert not library.exists()
    staged.unlink()
    staged.write_bytes(b"local")
    with pytest.raises(DirectLoanPublicationRefused, match="loan_direct_stage_scope_invalid"):
        publish(staged, work_dir=tmp_path, library_target_dir=library)
    assert staged.read_bytes() == b"local"


def test_symlinked_profile_destination_is_not_followed(tmp_path):
    staged, work, library = _stage(tmp_path)
    external = tmp_path / "other-profile"
    external.mkdir()
    library.parent.mkdir(parents=True)
    library.symlink_to(external, target_is_directory=True)
    with pytest.raises(DirectLoanPublicationRefused, match="loan_direct_stage_scope_invalid"):
        publish(staged, work_dir=work, library_target_dir=library)
    assert not list(external.iterdir())
    assert staged.exists()


def test_link_failure_keeps_staging_and_cleans_private_temporary(tmp_path, monkeypatch):
    staged, work, library = _stage(tmp_path)
    def fail_link(*_args, **_kwargs):
        raise PermissionError("synthetic private filesystem error")
    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(DirectLoanPublicationRefused, match="loan_direct_publication_failed"):
        publish(staged, work_dir=work, library_target_dir=library)
    assert staged.read_bytes() == b"synthetic provider bytes"
    assert library.is_dir()
    assert list(library.iterdir()) == []


def test_direct_loan_replaced_staging_path_is_not_deleted_after_publish(
    tmp_path, monkeypatch,
):
    staged, work, library = _stage(tmp_path, data=b"ORIGINAL LOAN")
    original_link = os.link
    swapped = []

    def rebind_staging_at_commit(src, dst, **kw):
        if not swapped:
            staged.unlink()
            staged.write_bytes(b"OTHER NEW STAGE")
            swapped.append(True)
        return original_link(src, dst, **kw)

    monkeypatch.setattr(os, "link", rebind_staging_at_commit)
    result = publish(
        staged, work_dir=work, library_target_dir=library,
    )
    assert result.read_bytes() == b"ORIGINAL LOAN"
    assert staged.read_bytes() == b"OTHER NEW STAGE"
    assert not list(library.glob(".loan-publish-*"))


def test_direct_loan_symlink_swap_of_temp_at_link_time_is_not_delivered(
    tmp_path, monkeypatch,
):
    staged, work, library = _stage(tmp_path, data=b"ORIGINAL LOAN")
    external = tmp_path / "secret-user.epub"
    external.write_bytes(b"PRIVATE OTHER USER")
    original_link = os.link

    def swap_temp_at_commit(src, dst, **kw):
        private = library / src
        private.unlink()
        private.symlink_to(external)
        return original_link(src, dst, **kw)

    monkeypatch.setattr(os, "link", swap_temp_at_commit)
    with pytest.raises(
        DirectLoanPublicationRefused, match="loan_direct_temp_changed",
    ):
        publish(staged, work_dir=work, library_target_dir=library)
    assert external.read_bytes() == b"PRIVATE OTHER USER"
    assert staged.read_bytes() == b"ORIGINAL LOAN"
    # Both substituted names are ambiguous and must not be cleaned as
    # Core-owned inodes. No success or local B receipt was returned.
    assert (library / "book.epub").is_symlink()
    assert all(path.is_symlink() for path in library.iterdir())


def test_direct_loan_same_size_new_stage_inode_before_open_is_refused(
    tmp_path, monkeypatch,
):
    staged, work, library = _stage(tmp_path, data=b"PAYLOAD A")
    original_open = os.open
    did_replace = []

    def swap_source_before_open(path, flags, *args, **kwargs):
        if Path(path) == Path(staged.name) and kwargs.get('dir_fd') is not None and not did_replace:
            old = work / "old.epub"
            staged.rename(old)
            staged.write_bytes(b"PAYLOAD B")
            did_replace.append(True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_source_before_open)
    with pytest.raises(
        DirectLoanPublicationRefused, match="loan_direct_stage_changed",
    ):
        publish(staged, work_dir=work, library_target_dir=library)
    assert staged.read_bytes() == b"PAYLOAD B"
    assert (work / "old.epub").read_bytes() == b"PAYLOAD A"
    assert not library.exists() or list(library.iterdir()) == []


def test_fsync_final_directory_before_stage_removal(tmp_path, monkeypatch):
    staged, work, library = _stage(tmp_path)
    library.mkdir(parents=True)
    real_fsync = os.fsync
    observed = []

    def check(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) and (library / staged.name).exists():
            assert staged.exists()
            assert (library / staged.name).read_bytes() == staged.read_bytes()
            observed.append("directory")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", check)
    result = publish(staged, work_dir=work, library_target_dir=library)
    assert observed == ["directory"]
    assert result.read_bytes() == b"synthetic provider bytes"
    assert not staged.exists()


def test_fsync_refusal_preserves_input_and_other_target(tmp_path, monkeypatch):
    staged, work, library = _stage(tmp_path)
    library.mkdir(parents=True)
    previous = library / "previous.epub"
    previous.write_bytes(b"older loan")
    real_fsync = os.fsync
    faults = []

    def fail(fd):
        if (not faults and stat.S_ISDIR(os.fstat(fd).st_mode)
                and (library / staged.name).exists()):
            faults.append(True)
            raise OSError("synthetic directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(DirectLoanPublicationRefused, match="loan_direct_publication_failed"):
        publish(staged, work_dir=work, library_target_dir=library)
    assert faults == [True]
    assert staged.read_bytes() == b"synthetic provider bytes"
    assert list(library.iterdir()) == [previous]
    assert previous.read_bytes() == b"older loan"


@pytest.mark.parametrize("phase", ["link", "fsync"])
def test_destination_rebound_during_commit_preserves_both_directories(tmp_path, monkeypatch, phase):
    staged, work, library = _stage(tmp_path)
    library.mkdir(parents=True)
    previous = library / "previous.epub"
    previous.write_bytes(b"older loan")
    moved = library.with_name("retired")
    replacement = b"foreign scope"
    mutations = []

    def replace_directory():
        if mutations:
            return
        library.rename(moved)
        library.mkdir()
        (library / staged.name).write_bytes(replacement)
        mutations.append(True)

    real_link = os.link
    real_fsync = os.fsync

    def link(src, dst, **kw):
        if phase == "link":
            replace_directory()
        return real_link(src, dst, **kw)

    def fsync(fd):
        if (phase == "fsync" and stat.S_ISDIR(os.fstat(fd).st_mode)
                and (library / staged.name).exists()):
            replace_directory()
        return real_fsync(fd)

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(DirectLoanPublicationRefused, match="loan_direct_target_scope_invalid"):
        publish(staged, work_dir=work, library_target_dir=library)
    assert mutations == [True]
    assert staged.read_bytes() == b"synthetic provider bytes"
    assert (library / staged.name).read_bytes() == replacement
    assert list(moved.iterdir()) == [moved / previous.name]
    assert (moved / previous.name).read_bytes() == b"older loan"


def test_nested_symlink_parent_does_not_create_inside_foreign_scope(tmp_path):
    staged, work, _library = _stage(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(DirectLoanPublicationRefused, match="loan_direct_stage_scope_invalid"):
        publish(staged, work_dir=work, library_target_dir=alias / "profile" / "provider")
    assert list(outside.iterdir()) == [] and staged.exists()
