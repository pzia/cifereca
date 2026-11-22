"""#7244/#976: read-only historical ADEPT SQL snapshots on idle connections.

A historical operator must never settle an unrelated transaction belonging to
its caller. SQLite connection contexts commit/roll back on exit, including
when a nested BEGIN fails. Capture and check the Core-owned connection under
its lock *before* entering that context.
"""
from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def historical_read_snapshot(storage):
    """Hold a fresh read transaction, or refuse without touching caller work."""
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise RuntimeError("adept_historical_nested_transaction")
        with db:
            db.execute("BEGIN")
            yield db
