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
