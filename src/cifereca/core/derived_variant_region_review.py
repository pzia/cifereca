"""#6937: bounded observation of private staging regions, never retention authority.

Every stage remains protected. A regular file without a known receipt is
unrecorded, NOT an orphan eligible for deletion. Physical space, exclusivity,
external references and recoverability cannot be inferred from this walk.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
from time import monotonic

from . import derived_variant_journal as journal
from .derived_variant_retention_review import _directory
from .derived_variant_stage_symlinks import _Incomplete, _walk, _stamp
from .library_inventory import _open_root

_SECONDS = 3
_CATEGORIES = ("pending_named", "committed_named", "unrecorded_regular",
               "ambiguous_entries", "unverified_regions", "unobserved_receipts")


def _report(status, counts=None):
    return {
        "status": status, "counts": counts if counts is not None else dict.fromkeys(_CATEGORIES, 0),
        "bounded_walk_complete": status == "observed",
        "exclusive_region_verified": False,
        "sha_verified": False, "external_dependencies_verified": False,
        "complete_physical_inventory": False, "reclaimable_bytes_known": False,
        "retention_authorized": False, "quota_refund_authorized": False,
        "destructive_cleanup_allowed": False,
    }


def _snapshot(storage, identity_id):
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise journal.DerivedJournalConflict("derived_region_nested_transaction")
        with db:
            db.execute("BEGIN")
            directory = _directory(db, identity_id)
            rows, _ = journal._measure(db)
            records = []
            for key, _size in rows:
                raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]
                # An unreadable receipt may name any stage; fail closed globally.
                intent = journal._decode(key, raw)
                records.append((key, journal._receipt_digest(raw), intent))
            return directory, tuple(records)


def _classify(profile, records, entries, identity_id):
    counts = dict.fromkeys(_CATEGORIES, 0)
    stage_refs = {}
    coordinate_refs = {}
    owned = {}
    for key, _digest, intent in records:
        stage_refs.setdefault(intent.staged_path, []).append((key, intent))
        for name in (intent.parent_path, intent.staged_path, intent.candidate_path):
            coordinate_refs.setdefault(name, set()).add(key)
        if intent.identity_id == identity_id:
            owned[key] = intent

    # Only direct children of exact reserved directory names are considered.
    # A misplaced subtree or non-directory region is ambiguous, not free.
    regions = set()
    for name, (info, _link) in entries.items():
        path = Path(name)
        if path.name == ".cifereca-staging" and not stat.S_ISDIR(info[2]):
            counts["unverified_regions"] += 1
        if path.name == "variants" and path.parent.name == ".cifereca-staging":
            if stat.S_ISDIR(info[2]):
                regions.add(path)
            else:
                counts["unverified_regions"] += 1

    matched = set()
    for name, (info, _link) in entries.items():
        path = Path(name)
        if path.parent not in regions:
            continue
        refs = stage_refs.get(name, ())
        matched.update(key for key, intent in refs if intent.identity_id == identity_id)
        if not stat.S_ISREG(info[2]):
            counts["ambiguous_entries"] += 1
        elif not refs:
            category = "ambiguous_entries" if name in coordinate_refs else "unrecorded_regular"
            counts[category] += 1
        elif len(refs) != 1:
            counts["ambiguous_entries"] += 1
        else:
            key, intent = refs[0]
            if (intent.identity_id != identity_id
                    or coordinate_refs[name] != {key}
                    or (info[0], info[1], info[3], info[4]) != intent.output_stamp
                    or not all(Path(value).is_relative_to(profile) and Path(value) != profile
                               for value in (intent.parent_path, intent.candidate_path))):
                counts["ambiguous_entries"] += 1
            else:
                phase = "pending_named" if key.startswith(journal._PREFIX) else "committed_named"
                counts[phase] += 1
    counts["unobserved_receipts"] = len(set(owned) - matched)
    return counts


def review_derived_private_region(storage, *, library_root, identity_id,
                                  explicitly_requested=False):
    """Stable, bounded no-follow observation in one physical profile.

    Use installation-wide receipt coordinates to avoid calling another
    owner's stage unrecorded. Compare both walks and full SQL receipt
    witnesses before returning aggregate counts. No directory or file is
    created, modified, hashed or deleted, and no path is returned.
    """
    if (explicitly_requested is not True or not isinstance(library_root, Path)
            or not library_root.is_absolute() or library_root == Path("/")
            or ".." in library_root.parts or type(identity_id) is not str
            or not 0 < len(identity_id) <= 1024
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in identity_id)):
        return _report("unavailable")
    fd = None
    try:
        deadline = monotonic() + _SECONDS
        fd = _open_root(library_root)
        root = _stamp(os.fstat(fd))
        snapshot = _snapshot(storage, identity_id)
        directory, records = snapshot
        profile = library_root / directory
        first = _walk(profile, (), deadline=deadline)
        counts = _classify(profile, records, first[1], identity_id)
        second = _walk(profile, (), deadline=deadline)
        if first != second or _snapshot(storage, identity_id) != snapshot:
            return _report("evidence_changed")
        current = _open_root(library_root)
        try:
            if _stamp(os.fstat(current)) != root:
                return _report("evidence_changed")
        finally:
            os.close(current)
        if monotonic() >= deadline:
            return _report("incomplete")
        return _report("observed", counts)
    except _Incomplete:
        return _report("incomplete")
    except (journal.DerivedJournalConflict, sqlite3.Error, OSError, TypeError,
            ValueError, RuntimeError, RecursionError, KeyError):
        return _report("unavailable")
    finally:
        if fd is not None:
            os.close(fd)
