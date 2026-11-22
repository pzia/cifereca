"""#4520: committed receipt retention preflight; SQL evidence only.

Never yields a release handle, path, digest, quota refund, or delete plan.
Filesystem, external dependency and retention-policy proofs remain separate.
"""
from __future__ import annotations

import sqlite3
from unicodedata import category

from .derived_variant_journal import (
    _read_logical_receipt,
    DerivedJournalConflict, _COMMITTED, _decode, _measure,
    _require_canonical_proof,
)


def _report(status, counts=None):
    return {
        "status": status,
        "counts": counts if counts is not None else {
            "canonical_sql_witness": 0,
            "dependent_receipt_or_staging_placement": 0,
            "canonical_unverified": 0,
        },
        "physical_proof_verified": False,
        "all_dependencies_verified": False,
        "retention_authorized": False,
        "quota_refund_authorized": False,
        "destructive_cleanup_allowed": False,
    }


def _directory(db, identity_id):
    rows = db.execute(
        "SELECT directory_name FROM identities WHERE identity_id=? LIMIT 2",
        (identity_id,),
    ).fetchall()
    directory = rows[0][0] if len(rows) == 1 else None
    if (type(directory) is not str or not 0 < len(directory) <= 256
            or any(category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                   for char in directory)
            or directory in (".", "..") or "/" in directory or chr(92) in directory
            or db.execute(
                "SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
                (directory,),
            ).fetchall() != [(identity_id,)]):
        raise DerivedJournalConflict("derived_retention_scope_unverified")
    return directory


def _canonical(storage, db, intent):
    parent_id = "sha256:" + intent.parent_sha256
    child_id = "sha256:" + intent.output_sha256
    if (db.execute(
            "SELECT artifact_id,identity_id,path,state FROM placements WHERE placement_id=?",
            (intent.parent_placement_id,),
        ).fetchone() != (parent_id, intent.identity_id, intent.parent_path, "present")
            or db.execute(
                "SELECT sha256,size FROM artifacts WHERE artifact_id=?", (parent_id,),
            ).fetchone() != (intent.parent_sha256, intent.parent_stamp[2])):
        raise DerivedJournalConflict("derived_retention_parent_unverified")
    placement_id = storage._stable_id("placement", intent.candidate_path)
    recipe_id = storage._stable_id(
        "variant", intent.parent_sha256, intent.transformer_id, intent.profile_id,
        intent.options_fingerprint,
    )
    origin_id = storage._stable_id(
        "origin", "derived", intent.identity_id, intent.parent_placement_id,
        placement_id, child_id, recipe_id,
    )
    _require_canonical_proof(db, intent, placement_id=placement_id,
                             origin_id=origin_id, recipe_id=recipe_id)


def _index_receipt_references(paths, inodes, key, intent):
    """Record only immutable receipt coordinates, not live FS evidence."""
    for path in (intent.parent_path, intent.staged_path, intent.candidate_path):
        paths.setdefault(path, set()).add(key)
    for stamp in (intent.parent_stamp, intent.output_stamp):
        inodes.setdefault(stamp[:2], set()).add(key)


def _other_receipt_dependency(paths, inodes, key, intent):
    # An exact recorded output inode used by a different receipt as either
    # input or output is a conservative veto, even if its path is distinct.
    # Sharing only an ordinary input parent remains permitted.
    return (any(paths[path] != {key} for path in (
                intent.staged_path, intent.candidate_path))
            or inodes[intent.output_stamp[:2]] != {key})


def review_derived_receipt_retention(storage, *, identity_id, explicitly_requested=False):
    """Classify committed receipts in a single bounded SQLite read snapshot.

    Shared paths with any other receipt (pending or committed, any identity)
    or an output inode recorded by another receipt are conservative blockers;
    sharing only the ordinary parent is allowed.
    This is NOT the full dependency inventory or a physical verification.
    Only aggregate counts for the requested exact profile are returned.
    """
    if (explicitly_requested is not True or type(identity_id) is not str
            or not 0 < len(identity_id) <= 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in identity_id)):
        return _report("unavailable")
    try:
        with storage._lock:
            db = storage._connect()
            if db.in_transaction:
                return _report("unavailable")
            with db:
                db.execute("BEGIN")
                _directory(db, identity_id)
                rows, _total = _measure(db)
                records = {}
                path_refs, inode_refs = {}, {}
                for key, _size in rows:
                    raw = _read_logical_receipt(db, key)
                    intent = _decode(key, raw)
                    records[key] = intent
                    _index_receipt_references(path_refs, inode_refs, key, intent)
                counts = dict.fromkeys((
                    "canonical_sql_witness", "dependent_receipt_or_staging_placement",
                    "canonical_unverified",
                ), 0)
                for key, intent in records.items():
                    if not key.startswith(_COMMITTED) or intent.identity_id != identity_id:
                        continue
                    try:
                        _canonical(storage, db, intent)
                    except DerivedJournalConflict:
                        counts["canonical_unverified"] += 1
                        continue
                    if (_other_receipt_dependency(path_refs, inode_refs, key, intent)
                            or db.execute(
                                "SELECT 1 FROM placements WHERE path=? LIMIT 1",
                                (intent.staged_path,),
                            ).fetchone() is not None):
                        counts["dependent_receipt_or_staging_placement"] += 1
                    else:
                        counts["canonical_sql_witness"] += 1
                return _report("observed_sql_only", counts)
    except (DerivedJournalConflict, sqlite3.Error, TypeError, ValueError,
            KeyError, RuntimeError, RecursionError):
        return _report("unavailable")
