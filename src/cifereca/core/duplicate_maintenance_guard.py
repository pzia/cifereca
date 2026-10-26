"""Fail-closed read-only dependency check before deleting a duplicate placement.

A SHA and matching owner scope do not guarantee that the *candidate path*
is unreferenced. The destructive maintenance broker must not silently drop
catalogue entries, supplier backups, recovery checkpoints or user declarations.
This checker never rewrites references; explicit relocation belongs to #3185.
"""
from __future__ import annotations

import json
import sqlite3

from .library_declared_links import DeclarationRejected, _records


def _mentions_exact_path(value: object, path: str, *, depth: int = 0) -> bool:
    if depth > 24:
        # Excessive nesting cannot be safely classified as no dependency.
        return True
    if isinstance(value, str):
        return value == path
    if type(value) is dict:
        return any(_mentions_exact_path(item, path, depth=depth + 1)
                   for item in value.values())
    if type(value) is list:
        return any(_mentions_exact_path(item, path, depth=depth + 1)
                   for item in value)
    return False


# Only these three additional canonical tables may be scanned. Keep the table
# names a closed internal allowlist; never accept SQL identifiers from plugins.
_EXTRA_METADATA_TABLES = frozenset({"entitlements", "artifacts", "placements"})


def canonical_metadata_mentions_path(
    db: sqlite3.Connection, path: str, *,
    tables: tuple[str, ...] = ("entitlements", "artifacts", "placements"),
) -> bool | None:
    """True if referenced, False if clear, None if evidence is incomplete.

    This is a *read-only* bounded check. An invalid JSON envelope or deep
    unknown structure is not proof that a path is free for rename/deletion.
    Path strings are exact: SHA and title do not authorize relinking.
    """
    if type(path) is not str or not path or any(
        table not in _EXTRA_METADATA_TABLES for table in tables
    ):
        return None
    for table in tables:
        for index, (raw,) in enumerate(db.execute(
            f"SELECT metadata_json FROM {table} LIMIT 100001"
        )):
            if index >= 100000 or type(raw) is not str or len(raw) > 1024 * 1024:
                return None
            try:
                value = json.loads(raw)
            except (ValueError, TypeError, RecursionError):
                return None
            if type(value) is not dict:
                return None
            if _mentions_exact_path(value, path):
                return True
    return False


def candidate_has_active_dependencies(storage, *, placement_id: str, path: str) -> bool:
    """True if deleting this path would orphan a known independent reference.

    Every query is local and read-only; ambiguity or an unavailable SQLite
    store returns *blocked*. The caller repeats this just before unlinking.
    Historical acquisition metadata is conservatively treated as a reference
    when it names the exact active path: it must be classified/migrated in a
    separate, explicit operation, not discarded by a one-click cleanup.
    """
    if type(path) is not str or not path or type(placement_id) is not str or not placement_id:
        return True
    try:
        with storage._lock, storage._connect() as db:
            if db.execute(
                "SELECT 1 FROM catalog_entries WHERE placement_id=? LIMIT 1",
                (placement_id,),
            ).fetchone():
                return True
            # A catalogue projection may name the candidate path in its
            # metadata despite referring to a different placement identifier.
            # Do not silently erase that operational pointer either.
            for (raw,) in db.execute("SELECT metadata_json FROM catalog_entries"):
                if type(raw) is not str or len(raw) > 1024 * 1024:
                    return True
                try:
                    value = json.loads(raw)
                except (ValueError, TypeError, RecursionError):
                    return True
                if _mentions_exact_path(value, path):
                    return True
            if db.execute(
                "SELECT 1 FROM backup_items WHERE local_path=? LIMIT 1",
                (path,),
            ).fetchone():
                return True
            for (raw,) in db.execute("SELECT metadata_json FROM acquisition_ledger"):
                if type(raw) is not str or len(raw) > 1024 * 1024:
                    return True
                try:
                    value = json.loads(raw)
                except (ValueError, TypeError, RecursionError):
                    # An unknown checkpoint may still contain an active path.
                    return True
                if _mentions_exact_path(value, path):
                    return True
            # An artifact variant may keep a physical source/output path
            # in metadata, independently of the candidate placement. A
            # matching SHA cannot justify unlinking that referenced name.
            # Metadata is not a typed active-vs-history contract yet, so
            # conservatively block an exact match or unreadable record.
            for index, (raw,) in enumerate(db.execute(
                "SELECT metadata_json FROM artifact_variants"
            )):
                if index >= 100_000 or type(raw) is not str or len(raw) > 1024 * 1024:
                    return True
                try:
                    value = json.loads(raw)
                except (ValueError, TypeError, RecursionError):
                    return True
                if type(value) is not dict or _mentions_exact_path(value, path):
                    return True
            # A right, immutable artifact or independently stored placement
            # may retain this exact physical path in its JSON metadata.
            # These records survive duplicate deletion and must not be orphaned.
            if canonical_metadata_mentions_path(db, path) is not False:
                return True
            # User declarations have their own strict private schema/budget,
            # and the source-bound file path is not a receipt we can replace.
            # An invalid declaration namespace cannot prove non-dependency.
            for record in _records(db):
                if (record["state"] == "active"
                        and record["file"]["path"] == path):
                    return True
            return False
    except (sqlite3.Error, OSError, DeclarationRejected):
        return True
