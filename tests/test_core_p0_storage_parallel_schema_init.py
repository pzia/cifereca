"""Core SQLite schema initialization is process-safe on an existing v17 DB.

No provider request, loan mutation or mocking of SQLite busy errors: this
exercise opens real independent connections/processes against one file.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from cifereca.storage import CURRENT_SCHEMA_VERSION, Storage


def _reopen_same_database(filename: str) -> int:
    store = Storage(Path(filename))
    try:
        return store.schema_version()
    finally:
        store.close(final=True)


def test_parallel_storage_init_serializes_idempotent_v17_sidecar_writes(tmp_path):
    path = tmp_path / "shared-init.sqlite"
    original = Storage(path)
    try:
        assert original.schema_version() == CURRENT_SCHEMA_VERSION
    finally:
        original.close(final=True)

    # Reproduces the shape of the #4082 multiprocess cycle: four separate
    # Storage(...) constructors on one already migrated database.
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_reopen_same_database, str(path))
            for _ in range(8)
        ]
        versions = [future.result(timeout=30) for future in futures]

    assert versions == [CURRENT_SCHEMA_VERSION] * 8
    check = Storage(path)
    try:
        assert check.schema_version() == CURRENT_SCHEMA_VERSION
        assert check.entitlements() == [] and check.acquisitions() == []
    finally:
        check.close(final=True)
