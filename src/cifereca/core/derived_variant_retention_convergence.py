"""#4520: per-receipt convergence of bounded SQL and physical observations.

No release handle, retention authority, complete dependency proof or bytes
reclaim estimate. Evidence is read-only and not an atomic FS/SQL inventory.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from time import monotonic

from .derived_variant_journal import (
    DerivedJournalConflict, _COMMITTED, _decode, _measure, _receipt_digest,
)
from .derived_variant_publication import DerivedPublicationConflict
from .derived_variant_retention_review import _canonical, _directory
from .derived_variant_retention_physical import _directory_stamp, _observe
from .library_inventory import _open_root

_LIMIT = 4
_MAX_BYTES = 1024 ** 3
_SECONDS = 3
_CATEGORIES = (
    "canonical_sql_unverified", "known_dependency", "physical_unverified",
    "linked_pair_observed", "stage_absent_target_verified",
    "additional_links_observed",
)


def _report(status, *, total=0, checked=0, counts=None):
    return {
        "status": status, "committed_receipts": total, "checked": checked,
        "counts": counts if counts is not None else dict.fromkeys(_CATEGORIES, 0),
        "complete_committed_page": status == "observed" and checked == total,
        "complete_physical_inventory": False, "all_dependencies_verified": False,
        "retention_authorized": False, "quota_refund_authorized": False,
        "destructive_cleanup_allowed": False, "reclaimable_bytes_known": False,
    }


def _snapshot(storage, identity_id):
    """One bounded SQL snapshot of all journal coordinates plus owner verdicts."""
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise DerivedJournalConflict("derived_retention_nested_snapshot")
        with db:
            db.execute("BEGIN")
            directory = _directory(db, identity_id)
            rows, _ = _measure(db)
            records, paths = [], {}
            for key, _size in rows:
                raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]
                intent = _decode(key, raw)
                records.append((key, _receipt_digest(raw), intent))
                for path in (intent.parent_path, intent.staged_path, intent.candidate_path):
                    paths.setdefault(path, set()).add(key)
            owned = []
            for key, digest, intent in records:
                if not key.startswith(_COMMITTED) or intent.identity_id != identity_id:
                    continue
                try:
                    _canonical(storage, db, intent)
                except DerivedJournalConflict:
                    verdict = "canonical_sql_unverified"
                else:
                    verdict = ("known_dependency" if (
                        any(paths[path] != {key} for path in (
                            intent.staged_path, intent.candidate_path))
                        or db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                                      (intent.staged_path,)).fetchone() is not None
                    ) else "canonical")
                owned.append((key, digest, intent, verdict))
            return directory, tuple((key, digest) for key, digest, _ in records), tuple(owned)


def review_converged_derived_retention(storage, *, library_root, identity_id,
                                       explicitly_requested=False):
    """Correlate the SAME receipt's SQL and physical evidence; never join counts.

    First four committed rows, max 1 GiB of attested input/output hashed,
    cooperative time bound between rows. SQL classifications and all journal
    receipt digests are rechecked while file descriptors are pinned and
    before returning. Evidence changes discard the entire observed page.
    """
    if (explicitly_requested is not True or not isinstance(library_root, Path)
            or not library_root.is_absolute() or library_root == Path("/")
            or ".." in library_root.parts or type(identity_id) is not str
            or not 0 < len(identity_id) <= 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in identity_id)):
        return _report("unavailable")
    root_fd = None
    try:
        deadline = monotonic() + _SECONDS
        root_fd = _open_root(library_root)
        stamp = _directory_stamp(root_fd)
        snapshot = _snapshot(storage, identity_id)
        directory, _journal, owned = snapshot
        profile = library_root / directory
        counts = dict.fromkeys(_CATEGORIES, 0)
        checked = budget = 0

        def current():
            if _snapshot(storage, identity_id) != snapshot:
                return False
            fd = _open_root(library_root)
            try:
                return _directory_stamp(fd) == stamp
            finally:
                os.close(fd)

        for _key, _digest, intent, verdict in owned[:_LIMIT]:
            if monotonic() >= deadline:
                break
            if verdict != "canonical":
                counts[verdict] += 1
            else:
                cost = intent.parent_stamp[2] + intent.output_size
                if cost > _MAX_BYTES - budget:
                    break
                budget += cost
                result = _observe(intent, profile, current)
                counts["physical_unverified" if result == "unverified" else result] += 1
            checked += 1
            if not current():
                return _report("evidence_changed")
        if not current():
            return _report("evidence_changed")
        return _report("observed", total=len(owned), checked=checked, counts=counts)
    except (DerivedJournalConflict, DerivedPublicationConflict, sqlite3.Error,
            OSError, TypeError, ValueError, RuntimeError, RecursionError, KeyError):
        return _report("unavailable")
    finally:
        if root_fd is not None:
            os.close(root_fd)
