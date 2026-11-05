"""P0 #3196: offline, read-only entry for the bounded 0.7 migration inventory.

Never construct Storage or CiferecaCore: their initializers may migrate SQLite,
create provider sessions or change files merely to answer an audit question.
The existing database must already have the current schema. No provider I/O,
content scan, acquisition or SQL write is performed here.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

from ..paths import database_file
from ..storage import CURRENT_SCHEMA_VERSION, Storage
from .legacy_backup_migration_inventory import (
    _blank, inventory_legacy_kobo_backups,
)


def inventory_existing_legacy_kobo(
    *, identity_id: str, database_path: Path | None = None,
) -> dict[str, int | str]:
    """Aggregate exact-profile historical Kobo rows from an *existing* DB.

    Status is complete only for a v14 database with exactly one registered
    identity and a bounded, consistent SQLite read. No raw ID, title, path,
    receipt, provider response, credential or account leaves this boundary.
    """
    if (type(identity_id) is not str
            or not 0 < len(identity_id) <= 1024
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in identity_id)):
        return _blank("invalid_scope")

    connection = None
    try:
        path = Path(database_path) if database_path is not None else database_file(create=False)
        # Read-only mode refuses a missing DB instead of manufacturing one.
        connection = sqlite3.connect(
            path.absolute().as_uri() + "?mode=ro",
            uri=True, timeout=10,
        )
        if Storage._read_schema_version(connection) != CURRENT_SCHEMA_VERSION:
            return _blank("schema_unavailable")
        owners = connection.execute(
            "SELECT identity_id FROM identities WHERE identity_id=? LIMIT 2",
            (identity_id,),
        ).fetchall()
        if len(owners) != 1:
            return _blank("invalid_scope")
        # Reuse the trusted, aggregate-only P0 inventory without invoking
        # Storage.__init__ (which would create/migrate a database).
        readonly = SimpleNamespace(
            _lock=threading.RLock(),
            _connect=lambda: connection,
            schema_version=lambda: CURRENT_SCHEMA_VERSION,
        )
        return inventory_legacy_kobo_backups(readonly, identity_id=identity_id)
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError):
        return _blank("unavailable")
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


__all__ = ["inventory_existing_legacy_kobo"]
