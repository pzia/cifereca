"""Read snapshot owned by the historical diagnostic, never its caller."""
from contextlib import contextmanager


@contextmanager
def historical_read_snapshot(storage):
    with storage._lock:
        db = storage._connect()
        # Storage uses thread-local connections. Entering the connection
        # context first can roll back a caller's pending transaction when
        # BEGIN fails. Refuse without touching that transaction.
        if db.in_transaction:
            raise ValueError("historical_adept_nested_transaction")
        with db:
            db.execute("BEGIN")
            yield db
