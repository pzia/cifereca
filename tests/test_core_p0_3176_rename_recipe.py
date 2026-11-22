"""#3176 isolated receipt of Core's existing rename writer, not a new UI writer.

The explicit dry-run remains incapable of execution. In this test only, a
separate maintenance command exercises the existing central writer on tmp_path.
"""
from pathlib import Path
import hashlib

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import LibraryMutationRequest
from cifereca.core.library_organizer_plan import prepare_organizer_dry_run, confirm_organizer_dry_run
from tests.test_core_p0_3171_atomic_publication_v14 import store, register
from tests.test_core_p0_3176_organizer_review import _service, _put_epub, _run


@pytest.mark.parametrize("fault", ["none", "sql", "collision"])
def test_confirmed_rename_recipe_existing_writer_postcheck(store, tmp_path, monkeypatch, fault):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    source, pid = _put_epub(store, root, "Acquisitions/old.epub")
    service = _service(store, root)
    scope, page = _run(service)
    plan = prepare_organizer_dry_run(scope, page, confirmed=True, rename_only=True)
    accepted = confirm_organizer_dry_run(scope, page, plan, confirmed=True)
    entry, = accepted.entries
    assert accepted.confirmed and not accepted.executable
    target = root / "A" / entry.proposed
    data = source.read_bytes()
    artifact = store.placement_get(pid)["artifact_id"]
    assert artifact == "sha256:" + hashlib.sha256(data).hexdigest()
    # No additional mutation API: this is a distinct central maintenance
    # request on an isolated fixture, not authority derived from dry-run.
    broker = LibraryMaintenanceBrokerImpl(service)
    native = broker.plan(LibraryMutationRequest("normalize_filename", placement_id=pid))
    assert native.facts["new_filename"] == target.name
    tables = ("source_references", "origin_links", "entitlements", "acquisition_ledger")
    def snapshot():
        with store._lock:
            db = store._connect()
            return {name: db.execute("SELECT * FROM " + name).fetchall() for name in tables}
    prior = snapshot()
    if fault == "sql":
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic SQL failure")
        monkeypatch.setattr(store, "relocate_local_path", fail)
    elif fault == "collision":
        target.write_bytes(b"unrelated")
    if fault == "none":
        result = broker.run(native)
        assert result.ok and result.changed == 1 and not result.warnings
        assert not source.exists() and target.read_bytes() == data
        placed = store.placement_for_path(target)
        assert placed["artifact_id"] == artifact and placed["identity_id"] == "A"
        assert all(row["artifact_id"] == artifact for row in store.catalog_entries(
            placement_id=placed["placement_id"]))
        assert store.placement_for_path(source) is None
        _, post = _run(service)
        assert all(f["reason"] != "rename_advice_only" for g in post["groups"] for f in g["files"])
    else:
        with pytest.raises(Exception):
            broker.run(native)
        assert source.read_bytes() == data and store.placement_get(pid)["path"] == str(source)
        assert target.read_bytes() == b"unrelated" if fault == "collision" else not target.exists()
    assert snapshot() == prior
