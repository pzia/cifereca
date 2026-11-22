"""Real synthetic FS/SQLite rename via the existing Core broker, no live fence."""
import pytest

from cifereca.core import library_organizer_execution as execute
from cifereca.core import canonical_recovery
from cifereca.core.library_organizer_plan import prepare_organizer_dry_run, confirm_organizer_dry_run
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import store, register
from tests.test_core_p0_3176_organizer_review import _service, _put_epub, _run


@pytest.mark.parametrize("case", ["success", "production", "collision", "stale"])
def test_exact_confirmed_rename_uses_existing_writer(store, tmp_path, monkeypatch, case):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    old, pid = _put_epub(store, root, "Acquisitions/old.epub", provider_id="local")
    data = old.read_bytes()
    row = store.placement_get(pid)
    assert row["provider_id"] == "local" and row["source_item_id"] == "Acquisitions/old.epub"
    scope, page = _run(_service(store, root))
    plan = prepare_organizer_dry_run(scope, page, confirmed=True, rename_only=True)
    accepted = confirm_organizer_dry_run(scope, page, plan, confirmed=True)
    new = old.with_name("Autrice - Roman commun.epub")
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(canonical_recovery, "acquisition_lock_file", lambda: lock_path)
    if case != "production":
        monkeypatch.setattr(execute, "_execution_fence_current",
                            lambda s, lock: s.root == root and s.storage is store)
    if case == "collision":
        new.write_bytes(b"other")
    elif case == "stale":
        store.metadata_set("unrelated", "changed")
    with InterProcessLock(lock_path) as lock:
        result = execute.execute_reviewed_rename(scope, page, accepted, shared_lock=lock, confirmed=True)
    if case == "success":
        assert result == {"status": "renamed", "changed": 1}
        assert new.read_bytes() == data and not old.exists()
        assert store.placement_get(pid)["path"] == str(new)
        assert store.placement_get(pid)["artifact_id"] == row["artifact_id"]
    else:
        assert result == {"status": "blocked", "changed": 0}
        assert old.read_bytes() == data and store.placement_get(pid) == row
        if case == "collision":
            assert new.read_bytes() == b"other"


@pytest.mark.parametrize("fault", ["sql_rollback", "old_unlink", "revoked_fence"])
def test_reviewed_rename_keeps_authoritative_copy_at_failure_boundary(
        store, tmp_path, monkeypatch, fault):
    """Exercise the existing writer through the reviewed wrapper, not a mock writer."""
    from pathlib import Path

    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    old, pid = _put_epub(store, root, "Acquisitions/old.epub", provider_id="local")
    data = old.read_bytes()
    baseline = store.placement_get(pid)
    scope, page = _run(_service(store, root))
    plan = prepare_organizer_dry_run(scope, page, confirmed=True, rename_only=True)
    accepted = confirm_organizer_dry_run(scope, page, plan, confirmed=True)
    assert accepted.confirmed and not accepted.executable
    entry, = accepted.entries
    new = root / scope.directory / entry.proposed
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(canonical_recovery, "acquisition_lock_file", lambda: lock_path)
    fence_checks = []

    def isolated_fence(s, lock):
        fence_checks.append((s, lock))
        return (s is scope and s.storage is store and s.root == root
                and (fault != "revoked_fence" or len(fence_checks) == 1))

    monkeypatch.setattr(execute, "_execution_fence_current", isolated_fence)
    tables = ("source_references", "origin_links", "entitlements", "acquisition_ledger")

    def authority():
        with store._lock:
            db = store._connect()
            return {name: db.execute("SELECT * FROM " + name).fetchall() for name in tables}

    before = authority()
    if fault == "sql_rollback":
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic relocation failure")
        monkeypatch.setattr(store, "relocate_local_path", fail)
    elif fault == "old_unlink":
        unlink = Path.unlink

        def fail_old_only(path, *args, **kwargs):
            if path == old:
                raise OSError("synthetic interruption after SQLite commit")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_old_only)

    with InterProcessLock(lock_path) as lock:
        result = execute.execute_reviewed_rename(
            scope, page, accepted, shared_lock=lock, confirmed=True)

    assert len(fence_checks) >= 2
    assert authority() == before
    assert old.read_bytes() == data
    if fault == "old_unlink":
        assert result == {"status": "recovery_required", "changed": None}
        assert new.read_bytes() == data and old.samefile(new)
        current = store.placement_get(pid)
        assert current["path"] == str(new)
        for key in ("artifact_id", "identity_id", "provider_id", "source_item_id", "state"):
            assert current[key] == baseline[key]
        assert store.placement_for_path(old) is None
        # A replay of the old review cannot erase either surviving name.
        with InterProcessLock(lock_path) as lock:
            assert execute.execute_reviewed_rename(
                scope, page, accepted, shared_lock=lock, confirmed=True
            ) == {"status": "blocked", "changed": 0}
        assert old.samefile(new) and new.read_bytes() == data
    else:
        expected = ({"status": "blocked", "changed": 0} if fault == "revoked_fence"
                    else {"status": "recovery_required", "changed": None})
        assert result == expected
        assert not new.exists()
        assert store.placement_get(pid) == baseline
    assert authority() == before


@pytest.mark.parametrize("case", ["success", "production", "cancelled", "lock_busy", "late_revocation"])
def test_native_worker_consumes_exact_review_once(store, tmp_path, monkeypatch, case):
    from types import SimpleNamespace as NS
    from cifereca.core.activation_scoped_broker import ActivationBrokerResolver
    from cifereca.core.maintenance_read_ports import maintenance_read_ports
    from cifereca.core.library_organizer_execution import admit_native_reviewed_rename
    from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl

    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    old, pid = _put_epub(store, root, "Acquisitions/old.epub", provider_id="local")
    data, baseline = old.read_bytes(), store.placement_get(pid)
    service = _service(store, root)
    key = "library.organizer.read"
    resolver = ActivationBrokerResolver(lambda _: None, maintenance_read_ports(service).get,
                                        "organizer", (key,))
    broker = resolver(key)
    plugin = object()
    resolved = NS(plugin_id="organizer", service=plugin, registry_generation=1)
    manager = NS(_registry_generation=1, service=lambda _: plugin,
                 _records={"organizer": NS(service=plugin, activation_brokers=resolver)})
    page = broker.preview(identity_id="A", confirmed=True)
    page = broker.prepare_dry_run(token=page.token, confirmed=True, rename_only=True)
    page = broker.confirm_dry_run(token=page.token, confirmed=True)
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(canonical_recovery, "acquisition_lock_file", lambda: lock_path)
    with InterProcessLock(lock_path):
        pass
    lock_bytes = lock_path.read_bytes()
    active = [True]
    def admit(confirmed):
        return admit_native_reviewed_rename(
            manager, resolved, page, service=service, identity_id="A",
            confirmed=confirmed, view_current=lambda: active[0])
    assert admit(False) is None
    command = admit(True)
    assert callable(command) and admit(True) is None
    if case != "production":
        monkeypatch.setattr(execute, "_execution_fence_current",
                            lambda s, lock: s.root == root and s.storage is store)
    if case == "cancelled":
        active[0] = False
    if case == "late_revocation":
        original = LibraryMaintenanceBrokerImpl.plan
        def revoke(self, request):
            result = original(self, request)
            active[0] = False
            return result
        monkeypatch.setattr(LibraryMaintenanceBrokerImpl, "plan", revoke)
    if case == "lock_busy":
        with InterProcessLock(lock_path, existing_nofollow=True):
            result = command()
    else:
        result = command()
    assert command() == {"status": "blocked", "changed": 0}
    assert lock_path.read_bytes() == lock_bytes
    new = old.with_name("Autrice - Roman commun.epub")
    if case == "success":
        assert result == {"status": "renamed", "changed": 1}
        assert new.read_bytes() == data and not old.exists()
        assert store.placement_get(pid)["path"] == str(new)
    else:
        assert result == {"status": "blocked", "changed": 0}
        assert old.read_bytes() == data and not new.exists()
        assert store.placement_get(pid) == baseline
