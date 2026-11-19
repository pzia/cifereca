"""#4520: bounded SQL-only external references to an exact staging name.

Only known Core tables are inspected; no plugin/database/filesystem inventory
claim. The caller owns a read transaction and has validated the exact receipts.
Only each stage's own committed receipt is exempt from the scan.
"""
from __future__ import annotations

import json
import posixpath
from urllib.parse import unquote, urlsplit

from .derived_variant_journal import _unique_pairs

_MAX_ROWS = 32768
_MAX_BYTES = 16 * 1024 * 1024
_MAX_ROW_BYTES = 1024 * 1024
_MAX_DEPTH = 24

# Closed, schema-owned allowlist (not supplied by a plugin).
_JSON_COLUMNS = (
    ("catalog_entries", "metadata_json"), ("acquisition_ledger", "metadata_json"),
    ("artifact_variants", "metadata_json"), ("artifacts", "metadata_json"),
    ("placements", "metadata_json"), ("entitlements", "metadata_json"),
    ("origin_links", "metadata_json"), ("source_references", "metadata_json"),
    ("device_placements", "metadata_json"), ("action_events", "facts_json"),
)


def _strings(value, depth=0):
    if depth > _MAX_DEPTH:
        raise ValueError("derived_stage_dependency_depth")
    if type(value) is str:
        yield value
    elif type(value) is dict:
        for key, item in value.items():
            yield key
            yield from _strings(item, depth + 1)
    elif type(value) is list:
        for item in value:
            yield from _strings(item, depth + 1)


def _matches_stage(text, stage):
    """Conservative lexical match, never resolve symlinks or access a URI.

    Relative paths with the known staging suffix are ambiguous across owners:
    block every selected matching suffix rather than guessing a base directory.
    This is not an exhaustive alias or external-consumer proof.
    """
    if stage in text:
        return True
    value = text.strip()
    if value.lower().startswith("file:"):
        uri = urlsplit(value)
        value = unquote(uri.path, errors="strict")
        # A non-local authority is not evidence of a different file.
    if not value or "\x00" in value:
        return False
    suffix = ".cifereca-staging/variants/" + posixpath.basename(stage)
    if not value.startswith("/") and posixpath.basename(stage) not in value:
        return False
    normalized = posixpath.normpath(value)
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    if normalized == stage:
        return True
    return (not normalized.startswith("/") and
            (normalized == suffix or normalized.endswith("/" + suffix)))


def scan_stage_references(db, selected):
    """Map up to four private stage names to conservative SQL observations.

    selected maps staging paths to their own already-decoded committed keys.
    Limits count *all* scanned rows and payload bytes, not just matches.
    Exhaustion, invalid/unknown JSON and excessive nesting are unverified;
    a positively observed reference remains a blocker. No exclusions for
    the destination or unrelated metadata, even within the same identity.
    """
    if not db.in_transaction or not 0 < len(selected) <= 4:
        raise ValueError("derived_stage_dependency_scope")
    status = dict.fromkeys(selected, "observed_clear")
    rows = remaining = 0

    def unknown():
        return {
            path: "referenced" if state == "referenced" else "unverified"
            for path, state in status.items()
        }

    # Exact explicit path references, irrespective of identity/state.
    for path in selected:
        for table, col in (
            ("placements", "path"), ("backup_items", "local_path"),
            ("device_placements", "target_relative_path"),
        ):
            if db.execute(f"SELECT 1 FROM {table} WHERE {col}=? LIMIT 1",
                          (path,)).fetchone() is not None:
                status[path] = "referenced"
                break

    # Each CASE protects Python payload materialization before checking the
    # remaining global budget. A bound miss is never an absence witness.
    queries = [("metadata", "key", "value", "metadata")]
    queries.extend((table, "NULL", col, "json") for table, col in _JSON_COLUMNS)
    queries.extend((table, "NULL", col, "path") for table, col in (
        ("placements", "path"), ("backup_items", "local_path"),
        ("device_placements", "target_relative_path"),
    ))
    for table, key_col, column, kind in queries:
        size = f"length(CAST({column} AS BLOB))"
        if key_col != "NULL":
            size += f"+length(CAST({key_col} AS BLOB))"
        where = f"WHERE {column} IS NOT NULL" if kind == "path" else ""
        for key, length, raw in db.execute(
            f"SELECT CASE WHEN {size}<=? THEN {key_col} END, {size}, "
            f"CASE WHEN {size}<=? THEN {column} END FROM {table} {where} LIMIT ?",
            (_MAX_ROW_BYTES, _MAX_ROW_BYTES, _MAX_ROWS - rows + 1),
        ):
            rows += 1
            if (rows > _MAX_ROWS or type(length) is not int
                    or length > _MAX_ROW_BYTES or length > _MAX_BYTES - remaining
                    or type(raw) is not str):
                return unknown()
            remaining += length
            # Scope exemptions per path, not blanket namespace exemptions.
            candidates = tuple(path for path in selected
                               if selected[path] != key and status[path] != "referenced")
            if not candidates:
                continue
            try:
                for path in candidates:
                    if (_matches_stage(raw, path)
                            or (type(key) is str and _matches_stage(key, path))):
                        status[path] = "referenced"
                if type(key) is str and (key.lstrip().startswith(("{", "[", '"')) or chr(92) in key):
                    for text in _strings(json.loads(key, object_pairs_hook=_unique_pairs)):
                        for path in candidates:
                            if _matches_stage(text, path):
                                status[path] = "referenced"
                if kind == "json" or (kind == "metadata" and (
                        raw.lstrip().startswith(("{", "[", '"')) or chr(92) in raw)):
                    parsed = json.loads(raw, object_pairs_hook=_unique_pairs)
                    if kind == "json" and type(parsed) is not dict:
                        return unknown()
                    for text in _strings(parsed):
                        for path in candidates:
                            if _matches_stage(text, path):
                                status[path] = "referenced"
            except (TypeError, ValueError, RecursionError):
                return unknown()
    return status
