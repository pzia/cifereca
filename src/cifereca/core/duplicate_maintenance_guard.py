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


def candidate_has_active_dependencies(
    storage, *, placement_id: str, path: str,
    removable_variant: dict | None = None,
) -> bool:
    """Fail closed when a candidate has independent SQLite references."""
    if type(path) is not str or not path or type(placement_id) is not str or not placement_id:
        return True
    try:
        with storage._lock, storage._connect() as db:
            return candidate_has_active_dependencies_in_db(
                db, placement_id=placement_id, path=path,
                removable_variant=removable_variant,
            )
    except (sqlite3.Error, OSError, DeclarationRejected):
        return True


def candidate_has_active_dependencies_in_db(
    db: sqlite3.Connection, *, placement_id: str, path: str,
    removable_variant: dict | None = None,
) -> bool:
    """Same fail-closed dependency check on an ALREADY-LOCKED transaction.

    Storage must call this only under its own lock and BEGIN IMMEDIATE,
    so a new SQLite reference cannot appear between evidence and commit.
    A parsing/SQLite failure propagates and rolls back the entire retire.
    """
    if type(path) is not str or not path or type(placement_id) is not str or not placement_id:
        return True
    # Ordinary duplicate deletion always preserves linked catalogue
    # rows. Deleting a *derived variant* is different: its one own,
    # explicitly marked projection is retired alongside its placement.
    # Never exempt another edition/account or a second catalogue row.
    owned_catalog_id = None
    linked = db.execute(
        "SELECT catalog_entry_id,artifact_id,identity_id,provider_id,"
        "item_id,metadata_json FROM catalog_entries WHERE placement_id=?",
        (placement_id,),
    ).fetchall()
    if linked:
        if (type(removable_variant) is not dict or len(linked) != 1
                or any(type(removable_variant.get(k)) is not str
                       or not removable_variant[k]
                       for k in ("artifact_id", "identity_id",
                                 "provider_id", "source_item_id",
                                 "source_artifact_id"))):
            return True
        cid, artifact, identity, provider, item, raw = linked[0]
        if (artifact, identity, provider, item) != (
            removable_variant["artifact_id"],
            removable_variant["identity_id"],
            removable_variant["provider_id"],
            removable_variant["source_item_id"],
        ):
            return True
        owned_catalog_id = cid
    # A catalogue projection may name this path even if it points to
    # a different placement. The one removable variant projection
    # must prove an exact direct path and no other nested reference.
    for cid, raw in db.execute(
        "SELECT catalog_entry_id,metadata_json FROM catalog_entries"
    ):
        if type(raw) is not str or len(raw) > 1024 * 1024:
            return True
        try:
            value = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            return True
        if type(value) is not dict:
            return True
        if cid == owned_catalog_id:
            if (value.get("variant") is not True
                    or value.get("source_artifact_id")
                       != removable_variant["source_artifact_id"]
                    or value.get("path") != path
                    or any(_mentions_exact_path(item, path)
                           for key, item in value.items()
                           if key != "path")):
                return True
            continue
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
    # Other Core metadata namespaces can retain an *operational* path
    # (for example an interrupted sync or a provider-specific checkpoint).
    # This generic key/value table was not covered by the existing guards.
    # A schema-free record cannot be safely migrated by a duplicate delete:
    # refuse a literal path, including one embedded in a JSON envelope.
    # Escaped Unicode/JSON slashes require decoding before deciding absence.
    for index, (raw,) in enumerate(db.execute(
        "SELECT value FROM metadata LIMIT 100001"
    )):
        if index >= 100_000 or type(raw) is not str or len(raw) > 1024 * 1024:
            return True
        if path in raw:
            return True
        if "\\u" in raw or "\\/" in raw:
            try:
                decoded = json.loads(raw)
            except (ValueError, TypeError, RecursionError):
                # Unknown escaped data may be a path we cannot interpret.
                return True
            if (_mentions_exact_path(decoded, path)
                    or path in json.dumps(decoded, ensure_ascii=False)):
                return True
    # User declarations have their own strict private schema/budget,
    # and the source-bound file path is not a receipt we can replace.
    # An invalid declaration namespace cannot prove non-dependency.
    for record in _records(db):
        if (record["state"] == "active"
                and record["file"]["path"] == path):
            return True
    return False
