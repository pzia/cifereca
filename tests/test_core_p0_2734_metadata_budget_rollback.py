"""#2734: revocation of stale subtrees must refund retained metadata budget.

Only synthetic EPUBs and intentionally moved test directories are involved.
No file path, title, account or metadata is written to Core public telemetry.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.library_inventory import InventoryLimits, scan_identity_roots
from tests.test_library_reconciliation import epub


@pytest.mark.parametrize("reuse_previous", [False, True])
def test_moved_subtree_refunds_metadata_budget_for_following_root(
        tmp_path, reuse_previous):
    first = tmp_path / "PRIVATE_ROOT_BEFORE"
    child = first / "PRIVATE_CHILD"
    second = tmp_path / "PRIVATE_ROOT_AFTER"
    child.mkdir(parents=True)
    second.mkdir()
    epub(child / "private-before.epub", title="PRIVATE_SYNTHETIC_TITLE")
    epub(second / "private-after.epub", title="PRIVATE_SYNTHETIC_TITLE")

    # Exactly enough capacity for one retained EPUB. Charging a subsequently
    # revoked EPUB would incorrectly reject the other root's valid metadata.
    expected = scan_identity_roots({"alice": (second,)})
    assert expected.reasons == set()
    assert len(expected.files) == 1
    metadata_budget = expected.metadata_bytes
    assert metadata_budget > 2
    assert expected.files[0].metadata

    reuse = None
    if reuse_previous:
        prior = scan_identity_roots({"alice": (first,)})
        assert prior.reasons == set()
        assert prior.metadata_bytes == metadata_budget
        reuse = {(row.identity_id, row.path): row for row in prior.files}

    relocated = tmp_path / "PRIVATE_MOVED_DURING_SCAN"
    moved = [False]

    def move_after_first_observation(counts):
        if counts["files"] == 1 and not moved[0]:
            moved[0] = True
            # The scanner holds an open no-follow directory descriptor, but
            # the *path* no longer names that inode; its whole subtree must
            # be invalidated before scanning the independent second root.
            child.rename(relocated)

    actual = scan_identity_roots(
        {"alice": (first, second)},
        limits=InventoryLimits(seconds=600, metadata_bytes=metadata_budget),
        progress=move_after_first_observation, _reuse=reuse,
    )
    assert moved == [True]
    assert "directory_changed" in actual.reasons
    assert "metadata_byte_budget" not in actual.reasons
    assert len(actual.files) == 1
    assert actual.files[0].path == str(second / "private-after.epub")
    assert actual.files[0].metadata == expected.files[0].metadata
    assert actual.metadata_bytes == expected.metadata_bytes
    assert actual.reused_files == 0
    assert actual.newly_observed_files == 1
    assert actual.redacted()["files"] == 1
    assert actual.redacted()["hashed_files"] == 1
    published = json.dumps(actual.redacted())
    assert "PRIVATE_" not in published
    assert str(child) not in published
    assert actual.files[0].sha256 not in published
