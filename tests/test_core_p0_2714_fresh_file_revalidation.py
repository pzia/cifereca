"""#2714: fresh nofollow/inode/SHA guard is not a repair or purchase proof."""
from __future__ import annotations

from dataclasses import replace
import json
import os

import pytest

from cifereca.core.library_fresh_file_check import revalidate_observed_file
from cifereca.core.library_inventory import scan_identity_roots
from tests.test_library_reconciliation import epub, filesystem


@pytest.fixture
def scanned(tmp_path):
    root = tmp_path / "PRIVATE-owned-root"
    root.mkdir()
    book = root / "PRIVATE_BOOK.epub"
    epub(book, title="PRIVATE synthetic book")
    inventory = scan_identity_roots({"alice": (root,)})
    assert len(inventory.files) == 1
    observation, = inventory.files
    assert observation.validation == "ok"
    assert observation.structure_validation == "epub_structure_crc_ok"
    assert observation.sha256
    return root, book, observation


def check(scanned, **kwargs):
    root, book, observation = scanned
    return revalidate_observed_file(
        observation, (root,), identity_id="alice", **kwargs,
    )


def test_unchanged_epub_is_fresh_without_writes_or_provenance_grant(scanned, tmp_path):
    before = filesystem(tmp_path)
    result = check(scanned)
    assert result.verified and result.reason == "unchanged"
    assert result.bytes_checked == scanned[2].size
    assert result.read_only is True
    assert result.attachment_authorized is False
    assert "PRIVATE" not in repr(result) + json.dumps(result.__dict__)
    assert scanned[2].sha256 not in repr(result)
    assert filesystem(tmp_path) == before


def test_modified_file_is_refused_even_after_previous_sha_passed(scanned):
    _root, book, _observation = scanned
    book.write_bytes(book.read_bytes() + b"x")
    result = check(scanned)
    assert result.reason == "file_changed"
    assert not result.verified and not result.attachment_authorized


def test_replaced_leaf_symlink_cannot_escape_to_foreign_book(scanned, tmp_path):
    root, book, observation = scanned
    foreign = tmp_path / "FOREIGN_BOOK.epub"
    epub(foreign)
    book.unlink()
    book.symlink_to(foreign)
    result = check(scanned)
    assert not result.verified
    assert result.reason == "path_unavailable"
    assert result.attachment_authorized is False


def test_symlink_ancestor_is_rejected_at_every_component(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    owned = root / "owned"
    owned.mkdir()
    epub(owned / "book.epub")
    observation, = scan_identity_roots({"alice": (root,)}).files
    backup = tmp_path / "backup"
    owned.rename(backup)
    owned.symlink_to(backup, target_is_directory=True)
    result = revalidate_observed_file(observation, (root,), identity_id="alice")
    assert result.reason == "path_unavailable" and not result.verified


def test_file_replaced_mid_hash_is_detected_by_fresh_path_reopen(scanned, tmp_path, monkeypatch):
    from cifereca.core import library_fresh_file_check as guard

    root, book, _observation = scanned
    original = os.read
    changed = [False]

    def replace_after_chunk(fd, count):
        data = original(fd, count)
        if not changed[0]:
            changed[0] = True
            newer = root / "replacement.epub"
            newer.write_bytes(book.read_bytes())
            os.replace(newer, book)  # new inode, same bytes
        return data

    monkeypatch.setattr(guard.os, "read", replace_after_chunk)
    result = check(scanned)
    assert changed[0]
    assert result.reason == "file_changed"
    assert not result.verified


def test_cross_identity_and_nonmatching_root_are_denied_before_open(scanned, tmp_path):
    root, _book, observation = scanned
    for owner, roots in (
        ("bob", (root,)),
        ("alice", (tmp_path / "other",)),
        # The caller must independently supply the current configured roots;
        # this byte guard cannot authorize/validate a changed config itself.
        ("alice", ()),
    ):
        result = revalidate_observed_file(
            observation, roots, identity_id=owner,
        )
        assert result.reason in ("invalid_scope", "file_changed", "path_unavailable")
        assert not result.verified
    # Do not accept an unresolved or impossible root capability.
    assert not revalidate_observed_file(
        observation, (root / "..",), identity_id="alice",
    ).verified


def test_cancelled_and_too_small_byte_budget_cannot_verify(scanned):
    cancelled = check(scanned, cancelled=lambda: True)
    assert cancelled.reason == "cancelled"
    limited = check(scanned, max_bytes=1)
    assert limited.reason == "byte_budget"
    assert not cancelled.verified and not limited.verified


@pytest.mark.parametrize("change", [
    {"sha256": None},
    {"fingerprint": ()},
    {"structure_validation": "not_checked"},
    {"validation": "file_changed"},
    {"media_format": "pdf"},
    {"size": -1},
])
def test_unverified_observation_never_becomes_fresh(scanned, change):
    root, _book, observation = scanned
    altered = replace(observation, **change)
    result = revalidate_observed_file(
        altered, (root,), identity_id="alice",
    )
    assert result.reason == "unverified_observation"
    assert not result.verified
    assert not result.attachment_authorized
