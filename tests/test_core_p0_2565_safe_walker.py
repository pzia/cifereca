"""#2565: no-follow, read-only, budgeted, non-authoritative enumeration."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cifereca.core.library_reconciliation_walk import walk_identity_root


def test_recursive_eligible_files_are_private_candidates_without_side_effects(tmp_path):
    root = tmp_path / "identity"
    (root / "nested" / "more").mkdir(parents=True)
    (root / "book.epub").write_bytes(b"fake epub")
    (root / "nested" / "more" / "other.PDF").write_bytes(b"fake pdf")
    (root / "nested" / "ignored.txt").write_bytes(b"not eligible")
    result = walk_identity_root(root)
    assert result.complete
    assert {row.path.relative_to(root).as_posix() for row in result.candidates} == {
        "book.epub", "nested/more/other.PDF",
    }
    assert all(row.size > 0 and row.device >= 0 for row in result.candidates)
    summary = str(result.redacted_summary())
    assert "book.epub" not in summary and str(root) not in summary
    assert result.redacted_summary()["mutation_authorized"] is False
    assert result.redacted_summary()["acquisition_authorized"] is False
    assert sorted(x.relative_to(root).as_posix() for x in root.rglob("*")) == [
        "book.epub", "nested", "nested/ignored.txt", "nested/more",
        "nested/more/other.PDF",
    ]


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="no nofollow platform")
def test_symlinked_root_ancestor_directory_and_file_never_cross_scope(tmp_path):
    outside = tmp_path / "other-identity"
    outside.mkdir()
    (outside / "secret.epub").write_bytes(b"secrets")
    inside = tmp_path / "identity"
    inside.mkdir()
    (inside / "real.epub").write_bytes(b"permitted")
    (inside / "external.epub").symlink_to(outside / "secret.epub")
    (inside / "foreign-dir").symlink_to(outside, target_is_directory=True)
    own = walk_identity_root(inside)
    assert own.complete
    assert [row.path.name for row in own.candidates] == ["real.epub"]
    ancestor = tmp_path / "link"
    ancestor.symlink_to(inside, target_is_directory=True)
    blocked = walk_identity_root(ancestor)
    assert not blocked.complete
    assert "directory_unavailable" in blocked.reasons
    assert blocked.candidates == ()


def test_entry_and_file_budgets_never_silently_report_completion(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    for i in range(12):
        (root / f"{i:02}.epub").write_bytes(b"sample")
    entry = walk_identity_root(root, max_entries=3)
    assert not entry.complete and "entry_budget" in entry.reasons
    assert entry.entries_inspected == 3
    assert len(entry.candidates) <= 3
    limited = walk_identity_root(root, max_files=4)
    assert not limited.complete and "file_budget" in limited.reasons
    assert len(limited.candidates) == 4
    assert limited.redacted_summary()["status"] == "incomplete_scan"


def test_depth_budget_is_explicit_without_opening_outside(tmp_path):
    root = tmp_path / "books"
    (root / "l1" / "l2").mkdir(parents=True)
    (root / "l1" / "l2" / "deep.epub").write_bytes(b"e")
    partial = walk_identity_root(root, max_depth=1)
    assert "depth_budget" in partial.reasons
    assert not partial.candidates


def test_cancellation_is_early_and_publicly_redacted(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    (root / "book.epub").write_bytes(b"content")
    calls = []
    def cancelled():
        calls.append(1)
        return len(calls) >= 2
    output = walk_identity_root(root, cancelled=cancelled)
    assert "cancelled" in output.reasons
    assert output.redacted_summary()["candidate_count"] == 0
    assert (root / "book.epub").read_bytes() == b"content"


def test_nonexistent_and_non_directory_roots_are_incomplete_not_empty_success(tmp_path):
    missing = walk_identity_root(tmp_path / "missing")
    assert missing.candidates == ()
    assert missing.reasons == ("directory_unavailable",)
    leaf = tmp_path / "file.epub"
    leaf.write_bytes(b"content")
    invalid = walk_identity_root(leaf)
    assert invalid.reasons == ("directory_unavailable",)


@pytest.mark.parametrize("options", [
    {"max_files": 0}, {"max_entries": 0}, {"max_depth": 0},
    {"max_files": "4"}, {"max_files": True}, {"max_entries": 100001},
    {"max_files": 20001}, {"max_depth": 65},
])
def test_invalid_budgets_fail_closed(tmp_path, options):
    with pytest.raises(ValueError, match="invalid_reconciliation_walk_budget"):
        walk_identity_root(tmp_path, **options)


def test_root_must_be_explicit_absolute_and_contained(tmp_path):
    for invalid in (Path("relative"), Path("relative/../root")):
        with pytest.raises(ValueError, match="invalid_reconciliation_identity_root"):
            walk_identity_root(invalid)


def test_file_inode_changes_are_not_interpreted_as_verified_rights(tmp_path):
    root = tmp_path / "identity"
    root.mkdir()
    file = root / "local.epub"
    file.write_bytes(b"first")
    row = walk_identity_root(root).candidates[0]
    file.unlink()
    file.write_bytes(b"second")
    # The scanner never announces proof of stable contents, entitlement,
    # account ownership, hash equivalence or permission for repair.
    assert row.path == file
    assert row.size == 5
    assert "rights" not in row.__dataclass_fields__
    assert "sha256" not in row.__dataclass_fields__


def test_523_files_fit_explicit_budget_without_touching_contents(tmp_path):
    root = tmp_path / "identity"
    root.mkdir()
    for i in range(523):
        (root / f"book-{i:04}.epub").write_bytes(b"synthetic")
    result = walk_identity_root(root, max_entries=1024, max_files=1024)
    assert result.complete
    assert len(result.candidates) == 523
    assert result.entries_inspected == 523
    assert result.directories_inspected == 1
