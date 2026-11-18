"""P1 #4983: closed local USB-source refusal codes over actual files.

Every refusal is a fixed code, never an absolute path, account, title, or SHA.
The same one-pass Core attestation underlies both the old API and UI dry-run.
No physical USB, provider, file modification or copy permission is inferred.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core import device_usb_dry_run as dry
from cifereca.core import device_usb_local_source as src
from tests.test_p1_2854_usb_dry_run_coordinator import (
    ARTIFACT, DIGEST, NOW, PAYLOAD, fake_scan, setup,
)
from tests.test_p1_2854_usb_local_source_sha import evidence


def inspect(*, artifact, placement, root):
    return src.inspect_local_usb_source(
        identity_id="alice", identity_root=root,
        artifact=artifact, placement=placement,
    )


def test_verified_local_epub_keeps_backward_compatible_sha_attestation(tmp_path):
    _old, artifact, placement, path = evidence(tmp_path)
    root = tmp_path / "alice"
    result = inspect(artifact=artifact, placement=placement, root=root)
    assert result.local is not None
    assert result.local == src.attest_local_usb_source(
        identity_id="alice", identity_root=root,
        artifact=artifact, placement=placement,
    )
    assert result.reason == "owned_source_sha_verified"
    assert not hasattr(result, "executable")
    assert not hasattr(result.local, "path")
    assert path.read_bytes()


@pytest.mark.parametrize("change,expected", (
    ("missing", "owned_source_file_missing"),
    ("wrong_size", "owned_source_size_mismatch"),
    ("wrong_same_size_sha", "owned_source_sha_mismatch"),
    ("leaf_symlink", "owned_source_file_not_regular"),
    ("parent_symlink", "owned_source_path_unverified"),
    ("wrong_artifact", "owned_source_artifact_unverified"),
    ("wrong_profile", "owned_source_scope_unverified"),
))
def test_actual_source_refusals_are_closed_codes(tmp_path, change, expected):
    _proof, artifact, placement, path = evidence(tmp_path)
    root = tmp_path / "alice"
    if change == "missing":
        path.unlink()
    elif change == "wrong_size":
        path.write_bytes(b"short")
    elif change == "wrong_same_size_sha":
        path.write_bytes(b"x" * artifact["size"])
    elif change == "leaf_symlink":
        path.unlink()
        other = tmp_path / "secret-bob.epub"
        other.write_bytes(b"foreign")
        path.symlink_to(other)
    elif change == "parent_symlink":
        external = tmp_path / "outside"
        external.mkdir()
        alias = root / "alias"
        alias.symlink_to(external, target_is_directory=True)
        placement["path"] = str(alias / "book.epub")
    elif change == "wrong_artifact":
        artifact["kind"] = "bundle"
    else:
        placement["identity_id"] = "bob"
    result = inspect(artifact=artifact, placement=placement, root=root)
    assert result.local is None
    assert result.reason == expected
    assert "/" not in result.reason
    assert "bob" not in result.reason
    assert artifact["sha256"] not in result.reason
    assert src.attest_local_usb_source(
        identity_id="alice", identity_root=root,
        artifact=artifact, placement=placement,
    ) is None


def test_local_mutation_during_hash_is_not_classified_as_sha_verified(
    tmp_path, monkeypatch,
):
    _proof, artifact, placement, path = evidence(tmp_path)
    original_read = src.os.read
    changed = [False]
    def mutate_after_first_read(fd, limit):
        data = original_read(fd, limit)
        if data and not changed[0]:
            changed[0] = True
            path.write_bytes(b"z" * artifact["size"])
        return data
    monkeypatch.setattr(src.os, "read", mutate_after_first_read)
    result = inspect(
        artifact=artifact, placement=placement, root=tmp_path / "alice",
    )
    assert changed == [True]
    assert result.local is None
    assert result.reason == "owned_source_changed_during_read"


@pytest.mark.parametrize("scenario,reason", (
    ("missing_file", "owned_source_file_missing"),
    ("wrong_sha", "owned_source_sha_mismatch"),
    ("no_placement", "owned_source_placement_missing"),
    ("ambiguous_placement", "owned_source_placement_ambiguous"),
))
def test_usb_dry_run_explains_local_refusal_without_copy_rights(
    tmp_path, monkeypatch, scenario, reason,
):
    db, root, book, mount = setup(tmp_path)
    monkeypatch.setattr(
        dry, "scan_managed_usb", lambda **_: fake_scan(),
    )
    if scenario == "missing_file":
        book.unlink()
    elif scenario == "wrong_sha":
        book.write_bytes(b"z" * len(PAYLOAD))
    elif scenario == "no_placement":
        for row in db.placements(state="present"):
            if row["artifact_id"] == ARTIFACT:
                db.placement_delete(row["placement_id"])
    else:
        duplicate = root / "duplicate.epub"
        duplicate.write_bytes(PAYLOAD)
        db.placement_put({
            "artifact_id": ARTIFACT, "identity_id": "alice",
            "path": str(duplicate), "provider_id": "local",
            "source_item_id": "duplicate",
            "state": "present", "metadata": {},
        })
    before = db.device_placements()
    result = dry.inspect_usb_dry_run(
        storage=db, identity_id="alice", device_id="reader-a",
        identity_root=root, mountpoint=mount, now=NOW,
    )
    assert result.status == "scanned" and result.scan_attested is True
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.action == "blocked"
    assert row.reason == reason
    assert row.observed == "missing"
    assert row.executable is False
    assert not result.executable
    assert db.device_placements() == before
    assert DIGEST not in row.reason
    assert str(root) not in row.reason


def test_diagnostic_exposed_as_bounded_french_label_without_private_source(
    tmp_path,
):
    from cifereca.gui.reader_usb_decisions import _REASON_LABELS
    for name in (
        "owned_source_placement_missing", "owned_source_placement_ambiguous",
        "owned_source_file_missing", "owned_source_sha_mismatch",
        "owned_source_changed_during_read", "owned_source_io_unverified",
    ):
        label = _REASON_LABELS[name]
        assert isinstance(label, str) and label
        assert str(tmp_path) not in label
        assert DIGEST not in label
