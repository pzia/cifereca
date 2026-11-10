"""#5225: direct loan staging must never clobber older valid library bytes."""
from __future__ import annotations

import os
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
