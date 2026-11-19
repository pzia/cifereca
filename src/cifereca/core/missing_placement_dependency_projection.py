"""#4708: private dependency projection over the same immutable SQLite image.

Observation only, not retirement readiness or an action token.
"""
from __future__ import annotations

import json
import sqlite3
import time
from urllib.parse import unquote, urlsplit

from .duplicate_maintenance_guard import (
    candidate_has_active_dependencies_in_db,
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
        # A device intent can keep a physical source dependency, even when
        # the exact local placement ID is not recorded in its metadata.
        ("SELECT 1 FROM device_placements WHERE artifact_id=? LIMIT 1", (artifact,)),
    ):
        if db.execute(sql, params).fetchone() is not None:
            return True
    return False


_MAX_REFERENCE_ROWS = 32768
_MAX_REFERENCE_BYTES = 16 * 1024 * 1024
_MAX_REFERENCE_ROW = 1024 * 1024

# Closed Core allowlist; metadata keys and JSON keys may themselves encode
# coordinates. No exemption for action history or another owner.
_REFERENCE_JSON = (
    ("origin_links", "metadata_json"), ("source_references", "metadata_json"),
    ("catalog_entries", "metadata_json"), ("acquisition_ledger", "metadata_json"),
    ("artifact_variants", "metadata_json"), ("artifacts", "metadata_json"),
    ("placements", "metadata_json"), ("entitlements", "metadata_json"),
    ("device_placements", "metadata_json"), ("action_events", "facts_json"),
)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous_reference_json")
        result[key] = value
    return result


def _strings(value, depth=0):
    if depth > 24:
        raise ValueError("historical_reference_depth")
    if type(value) is str:
        yield value
    elif type(value) is dict:
        for key, item in value.items():
            yield key
            yield from _strings(item, depth + 1)
    elif type(value) is list:
        for item in value:
            yield from _strings(item, depth + 1)


def _reference_matches(text, coordinates):
    if any(value in text for value in coordinates):
        return True
    if text.strip().lower().startswith("file:"):
        decoded = unquote(urlsplit(text.strip()).path, errors="strict")
        return any(value in decoded for value in coordinates)
    return False


def _extra_metadata_clear(db, coordinates, *, deadline, cancelled):
    """Bounded additional Core evidence; unknown or referenced both veto.

    Not a global proof against external SQLite databases, plugin storage,
    symlinks or all lexical aliases. No payload leaves the private snapshot.
    """
    if (not db.in_transaction or len(coordinates) != 2
            or any(type(v) is not str or not v for v in coordinates)):
        return False
    rows = used = 0
    queries = [("metadata", "key", "value", "metadata")]
    queries.extend((table, "NULL", col, "json") for table, col in _REFERENCE_JSON)
    queries.append(("device_placements", "NULL", "target_relative_path", "path"))
    for table, key_col, column, kind in queries:
        size = f"length(CAST({column} AS BLOB))"
        if key_col != "NULL":
            size += f"+length(CAST({key_col} AS BLOB))"
        where = f"WHERE {column} IS NOT NULL" if kind == "path" else ""
        for key, length, raw in db.execute(
            f"SELECT CASE WHEN {size}<=? THEN {key_col} END, {size}, "
            f"CASE WHEN {size}<=? THEN {column} END FROM {table} {where} LIMIT ?",
            (_MAX_REFERENCE_ROW, _MAX_REFERENCE_ROW, _MAX_REFERENCE_ROWS - rows + 1),
        ):
            rows += 1
            if (rows > _MAX_REFERENCE_ROWS or cancelled() or time.monotonic() >= deadline
                    or type(length) is not int or length > _MAX_REFERENCE_ROW
                    or length > _MAX_REFERENCE_BYTES - used or type(raw) is not str
                    or (kind == "metadata" and type(key) is not str)):
                return False
            used += length
            try:
                for text, json_required in ((raw, kind == "json"), (key, False)):
                    if text is None:
                        continue
                    if _reference_matches(text, coordinates):
                        return False
                    if (json_required or (kind == "metadata" and (
                            text.lstrip().startswith(("{", "[", '"')) or chr(92) in text))):
                        parsed = json.loads(text, object_pairs_hook=_unique_pairs)
                        if json_required and type(parsed) is not dict:
                            return False
                        if any(_reference_matches(v, coordinates) for v in _strings(parsed)):
                            return False
            except (ValueError, TypeError, RecursionError, UnicodeError):
                return False
    return not cancelled() and time.monotonic() < deadline


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
