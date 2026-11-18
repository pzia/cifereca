"""#4708: private dependency projection over the same immutable SQLite image.

Observation only, not retirement readiness or an action token.
"""
from __future__ import annotations

import json
import sqlite3
import time

from .duplicate_maintenance_guard import (
    _mentions_exact_path, candidate_has_active_dependencies_in_db,
)

_MAX_CANDIDATES = 512
_CLASSES = ("returned_restore_candidate", "historical_unlinked",
            "historical_dependent", "indeterminate", "root_unavailable")


def _direct_dependency(db, row):
    pid, path, owner, artifact = (row[k] for k in (
        "placement_id", "path", "identity_id", "artifact_id"))
    # Same SHA alone is not a reference to this Placement. A linked
    # catalogue, exact backup path or owning provenance warrants preservation.
    for sql, params in (
        ("SELECT 1 FROM catalog_entries WHERE placement_id=? LIMIT 1", (pid,)),
        ("SELECT 1 FROM backup_items WHERE local_path=? LIMIT 1", (path,)),
        ("SELECT 1 FROM placements WHERE path=? AND placement_id<>? LIMIT 1", (path, pid)),
        ("SELECT 1 FROM acquisition_ledger WHERE identity_id=? AND artifact_id=? LIMIT 1",
         (owner, artifact)),
        ("SELECT 1 FROM origin_links WHERE identity_id=? AND "
         "(artifact_id=? OR parent_artifact_id=?) LIMIT 1", (owner, artifact, artifact)),
        ("SELECT 1 FROM artifact_variants WHERE artifact_id=? OR source_artifact_id=? LIMIT 1",
         (artifact, artifact)),
    ):
        if db.execute(sql, params).fetchone() is not None:
            return True
    return False


def _extra_metadata_clear(db, coordinates, *, deadline, cancelled):
    # In addition to the existing guard, qualify origin/source envelopes.
    for table in ("origin_links", "source_references"):
        for i, (raw,) in enumerate(db.execute(
                f"SELECT metadata_json FROM {table} LIMIT 100001")):
            if (i >= 100000 or cancelled() or time.monotonic() >= deadline
                    or type(raw) is not str or len(raw) > 1024 * 1024):
                return False
            try:
                payload = json.loads(raw)
            except (ValueError, RecursionError):
                return False
            if type(payload) is not dict or any(
                    _mentions_exact_path(payload, value) for value in coordinates):
                return False
    return True


def project_missing_dependencies(db, rows, *, deadline, cancelled=lambda: False):
    """Annotate selected historical rows inside the query-only transaction.

    Guard refusal conflates references and unreadable evidence. Never turn
    that refusal into absence. Limit expensive candidates and share SQL deadline.
    """
    seen = 0
    for row in rows:
        if row.get("state") != "missing":
            continue
        row["historical_dependency"] = "indeterminate"
        seen += 1
        if seen > _MAX_CANDIDATES or cancelled() or time.monotonic() >= deadline:
            continue
        coords = tuple(row.get(k) for k in (
            "placement_id", "path", "identity_id", "artifact_id"))
        if any(type(value) is not str or not value for value in coords):
            continue
        pid, path, _, _ = coords
        try:
            if _direct_dependency(db, row):
                row["historical_dependency"] = "historical_dependent"
            elif (not candidate_has_active_dependencies_in_db(db, placement_id=pid, path=path)
                  and not candidate_has_active_dependencies_in_db(db, placement_id=pid, path=pid)
                  and _extra_metadata_clear(db, (path, pid), deadline=deadline, cancelled=cancelled)
                  and not cancelled() and time.monotonic() < deadline):
                row["historical_dependency"] = "historical_unlinked"
        except (sqlite3.Error, ValueError, RuntimeError, TypeError, RecursionError):
            continue


def aggregate_historical_missing(rows, paths, *, coverage_complete):
    counts = dict.fromkeys(_CLASSES, 0)
    for index, row in enumerate(rows):
        if row.get("state") != "missing":
            continue
        state = paths.states.get(index)
        reason = paths.observation_reasons.get(index, "")
        category = "indeterminate"
        if state == "stored_path_present":
            # Physical occupancy only; bytes/provenance need fresh proof.
            category = "returned_restore_candidate"
        elif state == "stored_path_unavailable" and reason.startswith("root_"):
            category = "root_unavailable"
        elif state == "stored_path_missing":
            evidence = row.get("historical_dependency")
            if evidence == "historical_dependent":
                category = evidence
            elif evidence == "historical_unlinked" and coverage_complete:
                category = evidence
        counts[category] += 1
    return {
        "coverage": "complete" if coverage_complete and not (
            counts["indeterminate"] or counts["root_unavailable"]) else "incomplete",
        "counts": counts,
        "retirement_authorized": False,
        "requires_fresh_confirmation": True,
    }
