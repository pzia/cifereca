"""#7407: the actual catalogue writers participate in Maintenance admission.

The normal Core composition uses synthetic SQLite/EPUB and an isolated XDG
state. No provider, production permission or patched execution fence.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from cifereca.config import Config
from cifereca.core.runtime import CiferecaCore
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.paths import acquisition_lock_file
from tests.test_core_p0_2835_epub_layout import _book
from tests.test_core_p0_4030_epub_evidence import META


def test_real_core_catalogue_writers_refuse_concurrent_maintenance(tmp_path, monkeypatch):
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.json"
    config = Config(config_path)
    config.set("library.directory", str(root))
    config.save()
    with CiferecaCore.open(
        config_path=config_path, database_path=tmp_path / "state.sqlite",
        builtin_ui=False, no_plugins=True, activate_plugins=False,
        diagnostics_enabled=False,
    ) as core:
        service = core.library
        owner = service.current_identity
        path = root / owner.directory_name / "Acquisitions" / "opaque.epub"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _book(META).getvalue()
        path.write_bytes(data)
        assert core.storage.placement_for_path(path) is None

        def scan():
            return service.catalog_scan()

        def index():
            return service.catalog_index_path(
                path, identity_id=owner.identity_id, provider_id="local",
            )

        lock_path = acquisition_lock_file()
        with InterProcessLock(lock_path):
            pass
        with InterProcessLock(lock_path, existing_nofollow=True) as maintenance:
            with ThreadPoolExecutor(max_workers=1) as pool:
                for consumer in (scan, index):
                    with pytest.raises(LockBusyError):
                        pool.submit(consumer).result(timeout=5)
                    assert maintenance.strict_current()
                    assert core.storage.placement_for_path(path) is None
                    assert path.read_bytes() == data

                # Canonical publication already holds the same lease on its
                # thread. The legitimate nested index/scan must still work
                # and must not release the outer Maintenance/Sync lock.
                assert index()["status"] == "indexed"
                assert scan()["storage_schema_version"] == core.storage.schema_version()
                assert maintenance.strict_current()
                with pytest.raises(LockBusyError):
                    pool.submit(scan).result(timeout=5)
            placement = core.storage.placement_for_path(path)
            assert placement and placement["identity_id"] == owner.identity_id
            assert path.read_bytes() == data
        assert scan()["storage_schema_version"] == core.storage.schema_version()
        assert path.read_bytes() == data
