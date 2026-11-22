"""#4520: bounded SQL-only external references to an exact staging name.

Only known Core tables are inspected; no plugin/database/filesystem inventory
claim. The caller owns a read transaction and has validated the exact receipts.
Only each stage's own committed receipt and its byte-exact, still-backed
compact shadow are exempt from the scan.
"""
from __future__ import annotations

import json
import posixpath
import re
from urllib.parse import unquote, urlsplit

from . import derived_variant_compact_receipt as compact
from .derived_variant_journal import _unique_pairs, _read_logical_receipt

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
    ("identities", "metadata_json"), ("provider_connections", "metadata_json"),
    ("provider_accounts", "metadata_json"), ("device_bindings", "metadata_json"),
    ("sync_runs", "detail_json"),
)

# Cache is JSON with arbitrary root types (including strings/arrays), and
# even an expired row can still be read by a legacy or external consumer.
# It is not a retention permission. Keep its keys in the same scan budget.
_STRUCTURED_COLUMNS = (("cache", "payload"), ("identities", "reading_languages_json"))
_TEXT_COLUMNS = (
    ("placements", "path"), ("backup_items", "local_path"),
    ("device_placements", "target_relative_path"), ("source_references", "evidence_ref"),
    ("origin_links", "evidence_ref"), ("cache", "namespace"), ("cache", "cache_key"),
    ("acquisition_ledger", "error_message"), ("backup_items", "error"),
)


# Schema-owned tables in Storage v17. The semantic columns above retain
# their stricter format-specific parsing. Residual columns may contain a
# path in an opaque identifier, title, label, status or later migration.
# Scan every residual TEXT/BLOB cell within the same global budget.
_SQL_TABLES = frozenset((
    "metadata", "cache", "backup_items", "entitlements", "artifacts", "placements",
    "acquisition_ledger", "catalog_entries", "identities", "provider_connections",
    "provider_accounts", "adept_profile_bindings", "device_bindings", "sync_runs",
    "device_placements", "artifact_variants", "action_events",
    "paris_borrower_adept_bindings", "source_references", "origin_links",
    "provider_cardinality_policies", "adept_borrower_grants",
    "paris_adept_grant_retirements", "provider_connection_epochs",
    "adept_borrower_grant_generations",
))
_SQL_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")


def _residual_columns(db):
    # Attached databases and unknown tables have no reviewed consumer
    # contract. They must not turn a partial search into a clear verdict.
    if any(name not in ("main", "temp") for _, name, _ in db.execute("PRAGMA database_list")):
        raise ValueError("derived_stage_dependency_schema")
    if db.execute("SELECT 1 FROM sqlite_temp_schema WHERE type='table' LIMIT 1").fetchone():
        raise ValueError("derived_stage_dependency_schema")
    tables = db.execute(
        "SELECT name FROM main.sqlite_schema WHERE type='table' "
        "AND substr(name,1,7)!='sqlite_' ORDER BY name LIMIT 65"
    ).fetchall()
    names = {row[0] for row in tables}
    if len(tables) > 64 or not names <= _SQL_TABLES:
        raise ValueError("derived_stage_dependency_schema")
    covered = set(_JSON_COLUMNS + _STRUCTURED_COLUMNS + _TEXT_COLUMNS)
    covered.update((("metadata", "key"), ("metadata", "value")))
    residual = []
    for table in sorted(names):
        fields = db.execute(
            "SELECT name,hidden FROM pragma_table_xinfo(?) LIMIT 129", (table,)
        ).fetchall()
        if not fields or len(fields) > 128:
            raise ValueError("derived_stage_dependency_schema")
        for column, hidden in fields:
            if (type(column) is not str or _SQL_IDENTIFIER.fullmatch(column) is None
                    or hidden != 0):
                raise ValueError("derived_stage_dependency_schema")
            if (table, column) not in covered:
                residual.append((table, column))
    return tuple(residual)


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
    basename = posixpath.basename(stage)
    if not value.startswith("/") and basename not in value:
        return False
    normalized = posixpath.normpath(value)
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    if normalized == stage:
        return True
    # A relative reference may use the stage directory itself as its base.
    # No source supplies an attested base, so even a bare basename or
    # "variants/name" is a blocker. Distinct absolute paths stay distinct.
    return not normalized.startswith("/") and posixpath.basename(normalized) == basename


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

    # A view is a consumer even if it materializes zero rows today.
    # Do not execute unreviewed SQL or attempt to interpret dynamic paths.
    # Inspect bounded definitions only, and preserve any positive observation.
    # Triggers may also embed a stage coordinate (including test/fault triggers).
    # Unknown triggers without a literal remain outside the external-consumer
    # proof, just like consumers outside this database.
    has_view = False
    for schema in ("main.sqlite_schema", "sqlite_temp_schema"):
        for object_type, length, definition in db.execute(
            f"SELECT type,length(CAST(sql AS BLOB)), "
            f"CASE WHEN length(CAST(sql AS BLOB))<=? THEN sql END "
            f"FROM {schema} WHERE type IN ('view','trigger') LIMIT ?",
            (_MAX_ROW_BYTES, _MAX_ROWS - rows + 1),
        ):
            rows += 1
            if (rows > _MAX_ROWS or type(length) is not int
                    or not 0 < length <= _MAX_ROW_BYTES or length > _MAX_BYTES - remaining
                    or type(definition) is not str):
                return unknown()
            remaining += length
            for path in status:
                # SQLite quotes literal apostrophes by doubling them.
                if path in definition or path.replace("'", "''") in definition:
                    status[path] = "referenced"
            if object_type == "view":
                # There is no currently reviewed view contract; continue
                # observing other definitions before returning unknown.
                has_view = True
    if has_view:
        return unknown()

    # Each CASE protects Python payload materialization before checking the
    # remaining global budget. A bound miss is never an absence witness.
    queries = [("metadata", "key", "value", "metadata")]
    queries.extend((table, "NULL", col, "json") for table, col in _JSON_COLUMNS)
    queries.extend((table, "NULL", col, "structured") for table, col in _STRUCTURED_COLUMNS)
    queries.extend((table, "NULL", col, "path") for table, col in _TEXT_COLUMNS)
    try:
        queries.extend((table, "NULL", col, "residual")
                       for table, col in _residual_columns(db))
    except (ValueError, TypeError):
        return unknown()
    for table, key_col, column, kind in queries:
        size = f"length(CAST({column} AS BLOB))"
        if key_col != "NULL":
            size += f"+length(CAST({key_col} AS BLOB))"
        where = (f"WHERE typeof({column}) IN ('text','blob')" if kind == "residual"
                 else f"WHERE {column} IS NOT NULL" if kind == "path" else "")
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
                if kind == "metadata" and type(key) is str and key.startswith(compact._PREFIX):
                    # A shadow is historical receipt data, not a second consumer,
                    # only with the exact authoritative logical original:
                    # physical receipt or validated atomic retirement marker.
                    # Never exempt an orphan or another stage's proof.
                    original_key, original = compact.decode_committed_witness(key, raw)
                    backing = _read_logical_receipt(db, original_key)
                    if backing != original:
                        return unknown()
                    candidates = tuple(path for path in candidates
                                       if selected[path] != original_key)
                for path in candidates:
                    if (_matches_stage(raw, path)
                            or (type(key) is str and _matches_stage(key, path))):
                        status[path] = "referenced"
                if type(key) is str and (key.lstrip().startswith(("{", "[", '"')) or chr(92) in key):
                    for text in _strings(json.loads(key, object_pairs_hook=_unique_pairs)):
                        for path in candidates:
                            if _matches_stage(text, path):
                                status[path] = "referenced"
                if kind in ("json", "structured") or (kind in ("metadata", "path", "residual") and (
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
