"""#6939: a final Storage teardown cannot close a live journal transaction.

Real SQLite/APSW, deterministic thread handoff; no provider transport.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from cifereca import storage as storage_module
from cifereca.storage import Storage


@pytest.mark.parametrize("final", [False, True])
def test_close_waits_for_current_transaction_and_fences_worker(tmp_path, final):
    storage = Storage(tmp_path / "shutdown.sqlite")
    entered, release, closing, closed, resume = (Event() for _ in range(5))
    original = []

    def write_then_return():
        with storage._lock, storage._connect() as db:
            original.append(db)
            db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("fence", "committed"))
            entered.set()
            assert release.wait(10)
        assert resume.wait(10)
        if final:
            with pytest.raises(storage_module.sqlite3.ProgrammingError, match="storage_closed"):
                storage.metadata_set("late", "must not commit")
        else:
            with storage._lock, storage._connect() as db:
                assert db is not original[0]
                assert db.execute("SELECT value FROM metadata WHERE key='fence'").fetchone() == ("committed",)

    def close():
        closing.set()
        storage.close(final=final)
        closed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(write_then_return)
        closer = None
        try:
            assert entered.wait(10)
            closer = pool.submit(close)
            assert closing.wait(10)
            assert not closed.wait(.05), "close raced the active transaction"
            release.set()
            closer.result(timeout=10)
            assert closed.is_set()
        finally:
            release.set()
            resume.set()
        worker.result(timeout=10)
    try:
        if final:
            # Even a new caller cannot reopen a definitively closed instance.
            with pytest.raises(storage_module.sqlite3.ProgrammingError, match="storage_closed"):
                storage.metadata_get("fence")
            storage.close()
            with pytest.raises(storage_module.sqlite3.ProgrammingError, match="storage_closed"):
                storage.metadata_get("fence")
        else:
            assert storage.metadata_get("fence") == "committed"
        with ThreadPoolExecutor(max_workers=1) as pool:
            if final:
                with pytest.raises(storage_module.sqlite3.ProgrammingError, match="storage_closed"):
                    pool.submit(storage.metadata_get, "fence").result(timeout=10)
        reopened = Storage(storage.path)
        try:
            assert reopened.metadata_get("fence") == "committed"
            assert reopened.metadata_get("late") is None
        finally:
            reopened.close()
    finally:
        storage.close()


def test_reusable_close_does_not_reuse_other_thread_closed_handle(tmp_path):
    storage = Storage(tmp_path / "reusable.sqlite")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            def connection():
                with storage._lock, storage._connect() as db:
                    return db
            old = pool.submit(connection).result(timeout=10)
            storage.close()
            new = pool.submit(connection).result(timeout=10)
            assert old is not new
            assert pool.submit(storage.schema_version).result(timeout=10) == storage.schema_version()
    finally:
        storage.close()
