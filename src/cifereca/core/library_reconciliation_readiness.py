"""Cheap, closed-result readiness for the offline reconciliation UI (#2661).

The test is intentionally NOT an audit. It never opens files for writing, never
reads providers, and never starts Core services. Call it from TaskRunner, not
GTK; even the optional single metadata query is protected from GUI stalls.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

from .library_audit_roots import configured_identity_roots


@dataclass(frozen=True, repr=False)
class ReconciliationReadiness:
    status: str
    reason: str

    @property
    def enabled(self) -> bool:
        # Unknown is a controlled attempt, not an indefinite disabled button.
        # The actual Core broker revalidates all prerequisites during the scan.
        return self.status in ('ready_offline', 'indeterminate')

    def __repr__(self):
        return '<ReconciliationReadiness closed>'


def local_reconciliation_readiness(service, identity_id) -> ReconciliationReadiness:
    """Validate local preconditions without reading book proofs.

    Never require internet, an account token, first sync, downloaded files or
    providers. A database that exists can yield an honest incomplete/empty
    audit. Runtime SQLite/WAL failures are deliberately NOT prediagnosed here.
    """
    if type(identity_id) is not str or not identity_id:
        return ReconciliationReadiness('blocked', 'identity_missing')
    try:
        current = service.current_identity
        if current is None or current.identity_id != identity_id:
            return ReconciliationReadiness('blocked', 'identity_not_active')
        config = service.config
        configured_identity_roots(config, identity_id)
    except (AttributeError, TypeError, ValueError, KeyError):
        return ReconciliationReadiness('blocked', 'identity_configuration')
    except Exception:
        return ReconciliationReadiness('indeterminate', 'configuration_unchecked')
    try:
        storage = service.storage
        path = Path(storage.path)
        if not path.is_absolute() or '..' in path.parts:
            return ReconciliationReadiness('blocked', 'storage_invalid')
    except (AttributeError, TypeError, ValueError):
        return ReconciliationReadiness('initializing', 'storage_initializing')
    try:
        source = os.lstat(path)
    except FileNotFoundError:
        return ReconciliationReadiness('initializing', 'storage_initializing')
    except PermissionError:
        return ReconciliationReadiness('indeterminate', 'storage_unchecked')
    except OSError:
        return ReconciliationReadiness('indeterminate', 'storage_unchecked')
    if not stat.S_ISREG(source.st_mode):
        return ReconciliationReadiness('blocked', 'storage_invalid')

    # IMPORTANT: storage.metadata_get() opens a writable Core connection and
    # can create SQLite sidecars. A readiness check must never do this.
    # Immutable SQLite reads *only* the base image: no WAL/SHM, network, lock
    # contention, migration, cache, schema change or source-side write.
    # A live WAL might contain a newer journal: in that case the gate is
    # unknown/allowed and the real no-write audit must revalidate it.
    try:
        wal = os.lstat(str(path) + '-wal')
    except FileNotFoundError:
        wal = None
    except OSError:
        return ReconciliationReadiness('indeterminate', 'migration_unchecked')
    if wal is not None:
        return ReconciliationReadiness('indeterminate', 'migration_unchecked')
    uri = 'file:' + quote(str(path), safe='/') + '?mode=ro&immutable=1'
    try:
        with sqlite3.connect(uri, uri=True, timeout=0.15) as reader:
            reader.execute('PRAGMA query_only=ON')
            exists = reader.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='metadata' LIMIT 1"
            ).fetchone()
            pending = (reader.execute(
                "SELECT 1 FROM metadata WHERE key=? "
                "AND value IS NOT NULL AND value<>'' LIMIT 1",
                ('identity-directory-migration-v1',),
            ).fetchone() if exists else None)
    except sqlite3.Error:
        return ReconciliationReadiness('indeterminate', 'migration_unchecked')
    if pending:
        return ReconciliationReadiness('temporarily_busy', 'migration_pending')
    return ReconciliationReadiness('ready_offline', 'local_ready')
