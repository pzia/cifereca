"""#4708: private single-placement archive proposal, never an action token."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import time

from .library_inventory import _open_root
from .missing_placement_retirement import _evidence, _directory_signature
from .missing_placement_readiness import _physical_absence
from .missing_placement_dependency_projection import (
    _direct_dependency, _extra_metadata_clear,
)


def _signature(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def review_missing_archive_candidate(storage, *, library_root: Path,
                                     identity_id: str, placement_id: str):
    """Explicit candidate, no scan, no plan token, no mutation or authority.

    Preserve the existing retirement guard and additionally require the
    historical direct/metadata dependency checks. A positive result is a
    proposal only, constrained to this read snapshot and absence observation.
    """
    def report(state, reason):
        return {
            "proposal": state, "reason": reason,
            "retirement_authorized": False, "archive_authorized": False,
            "requires_fresh_confirmation": True,
        }

    if (not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts
            or any(type(item) is not str or not 0 < len(item) <= 256
                   or any(ord(ch) < 32 for ch in item)
                   for item in (identity_id, placement_id))):
        return report("preserve", "invalid_scope")
    fd = None
    try:
        fd = _open_root(library_root)
        info = os.fstat(fd)
        # A read transaction prevents cross-snapshot combinations of SQL
        # facts. No SQL write or use of the legacy deletion entry point.
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            result, evidence = _evidence(
                db, root=library_root, root_stat=info,
                identity_id=identity_id, placement_id=placement_id,
            )
            if evidence is None:
                return report("preserve", result["reason"])
            _private_token, path, profile, _revision, profile_sig = evidence
            row = db.execute(
                "SELECT artifact_id FROM placements WHERE placement_id=? AND identity_id=?",
                (placement_id, identity_id),
            ).fetchone()
            if row is None or type(row[0]) is not str or not row[0]:
                return report("preserve", "artifact_unknown")
            if _direct_dependency(db, {
                "placement_id": placement_id, "path": path,
                "identity_id": identity_id, "artifact_id": row[0],
            }):
                return report("preserve", "historical_dependency")
            if not _extra_metadata_clear(
                db, (path, placement_id), deadline=time.monotonic() + 2,
                cancelled=lambda: False,
            ):
                return report("preserve", "additional_evidence_unavailable")
            if (_physical_absence(profile, path) != "absent"
                    or _directory_signature(profile) != profile_sig):
                return report("preserve", "physical_evidence_changed")
            current = _open_root(library_root)
            try:
                if _signature(current) != _signature(fd):
                    return report("preserve", "root_changed")
            finally:
                os.close(current)
        # No returned identifier, action token or assertion of future safety.
        return report("supervised_archive_candidate", "no_dependency_observed")
    except (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError, RecursionError):
        return report("preserve", "evidence_unavailable")
    finally:
        if fd is not None:
            os.close(fd)
