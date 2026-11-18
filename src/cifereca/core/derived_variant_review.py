"""#4520: review exact private pending receipts, never replay or clean up."""
from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import stat

from .derived_variant_journal import (
    DerivedJournalConflict, _read_pending_reservation,
)
from .derived_variant_publication import (
    DerivedPublicationConflict, _verified_variant_file, _file_signature,
)
from .library_inventory import _open_root

_MAX_REVIEW_BYTES = 1024 ** 3


def _snapshot(storage, reservation, identity_id):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        _raw, intent = _read_pending_reservation(db, reservation)
        if intent.identity_id != identity_id:
            raise DerivedJournalConflict("derived_review_scope_mismatch")
        profiles = db.execute(
            "SELECT directory_name FROM identities WHERE identity_id=? LIMIT 2",
            (identity_id,),
        ).fetchall()
        if len(profiles) != 1:
            raise DerivedJournalConflict("derived_review_scope_mismatch")
        directory = profiles[0][0]
        if (type(directory) is not str or not directory or directory in (".", "..")
                or "/" in directory or chr(92) in directory):
            raise DerivedJournalConflict("derived_review_scope_mismatch")
        owners = db.execute(
            "SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
            (directory,),
        ).fetchall()
        if owners != [(identity_id,)]:
            raise DerivedJournalConflict("derived_review_scope_mismatch")
        parent_id = "sha256:" + intent.parent_sha256
        parent = db.execute(
            "SELECT artifact_id,identity_id,path,state FROM placements WHERE placement_id=?",
            (intent.parent_placement_id,),
        ).fetchone()
        artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?", (parent_id,),
        ).fetchone()
        if (parent != (parent_id, identity_id, intent.parent_path, "present")
                or artifact != (intent.parent_sha256, intent.parent_stamp[2])):
            raise DerivedJournalConflict("derived_review_parent_changed")
        if db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                      (intent.candidate_path,)).fetchone():
            raise DerivedJournalConflict("derived_review_target_claimed")
    return intent, directory


def _target_observation(path, stage_signature):
    folder = _open_root(path.parent)
    try:
        try:
            info = os.stat(path.name, dir_fd=folder, follow_symlinks=False)
        except FileNotFoundError:
            return "staged_only"
        if not stat.S_ISREG(info.st_mode) or _file_signature(info) != stage_signature:
            raise DerivedJournalConflict("derived_review_target_changed")
        return "linked_pending"
    finally:
        os.close(folder)


def review_pending_derived_publication(storage, *, library_root, identity_id, reservation):
    """Check one approved-for-review receipt without changing bytes or SQL.

    A pending intent is not proof of canonical publication. An intact signed
    inode witness can identify either a pre-link interruption or an actual
    same-inode hardlink with no SQL Placement. The result is an observation,
    not permission to link again, invoke a transformer, commit or delete.
    Callers must supply the configured root and exact active identity; this
    is not a plugin or public UI capability. No paths or hashes are returned.
    """
    def result(status, reason):
        return {"status": status, "reason": reason, "recovery_authorized": False,
                "destructive_cleanup_allowed": False}

    if (not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts
            or type(identity_id) is not str or not identity_id):
        return result("manual_review", "scope_unverified")
    try:
        intent, directory = _snapshot(storage, reservation, identity_id)
        parent, stage, target = map(Path, (intent.parent_path, intent.staged_path,
                                         intent.candidate_path))
        profile = library_root / directory
        if not all(path.is_relative_to(profile) and path != profile
                   for path in (parent, stage, target)):
            return result("manual_review", "scope_unverified")
        if intent.parent_stamp[2] + intent.output_size > _MAX_REVIEW_BYTES:
            return result("manual_review", "review_budget_exceeded")
        # No-follow hashes are bounded by the receipt's exact sizes.
        # Hold both file descriptors while rechecking SQL and the named
        # candidate. A matching SHA at a different inode is never enough.
        with (
            _verified_variant_file(parent, intent.parent_sha256,
                                   expected_size=intent.parent_stamp[2]) as parent_current,
            _verified_variant_file(stage, intent.output_sha256,
                                   expected_size=intent.output_size) as stage_current,
        ):
            original = parent_current()
            staged = stage_current()
            if ((original[0], original[1], *original[3:]) != intent.parent_stamp
                    or (staged[0], staged[1], *staged[3:5]) != intent.output_stamp):
                return result("manual_review", "physical_proof_changed")
            first = _target_observation(target, staged)
            if _snapshot(storage, reservation, identity_id) != (intent, directory):
                return result("manual_review", "scope_changed")
            if parent_current() != original or stage_current() != staged:
                return result("manual_review", "physical_proof_changed")
            second = _target_observation(target, staged)
            if second != first:
                return result("manual_review", "physical_proof_changed")
        return result(first, "observed_not_authorized")
    except (DerivedJournalConflict, DerivedPublicationConflict, OSError,
            sqlite3.Error, ValueError, TypeError, RuntimeError, RecursionError):
        return result("manual_review", "evidence_unverified")
