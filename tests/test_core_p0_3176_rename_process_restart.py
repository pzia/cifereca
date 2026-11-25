"""Restart an independent process across the rename writer's commit boundary."""
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cifereca.storage import Storage
from tests.test_core041_artifact_authority import Control
from tests.test_core_p0_2789_crash_consistent_normalize import _case


def crash_writer(root, boundary):
    service, _, broker, plan, _, _, _ = _case(root)
    if boundary == "before_commit":
        def terminate_before_commit(statement):
            # sqlite invokes this callback before executing COMMIT, after
            # the real relocation has updated the rows in its transaction.
            if statement == "COMMIT":
                os._exit(41)

        service.storage._connect().set_trace_callback(terminate_before_commit)
    elif boundary == "after_cleanup":
        original = Path.unlink
        old = root / "library" / "Book.epub"

        def terminate_after_cleanup(path, *args, **kwargs):
            original(path, *args, **kwargs)
            if path == old:
                os._exit(43)

        Path.unlink = terminate_after_cleanup
    else:
        relocate = service.storage.relocate_local_path

        def terminate_after_commit(*args, **kwargs):
            relocate(*args, **kwargs)
            os._exit(42)

        service.storage.relocate_local_path = terminate_after_commit
    broker.run(plan, Control())
    raise AssertionError("writer did not reach the expected interruption")


@pytest.mark.parametrize("boundary,expected_exit,name", [
    ("before_commit", 41, "Book.epub"),
    ("after_commit", 42, "Author - Book.epub"),
    ("after_cleanup", 43, "Author - Book.epub"),
])
def test_fresh_storage_resolves_authoritative_bytes_after_writer_exit(
        tmp_path, boundary, expected_exit, name):
    repo = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(repo / "src"), *sys.path)),
    }
    program = (
        "import sys; from pathlib import Path; "
        "from tests.test_core_p0_3176_rename_process_restart import crash_writer; "
        "crash_writer(Path(sys.argv[1]), sys.argv[2])"
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), boundary],
        cwd=repo, env=environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == expected_exit, result.stderr
    root = tmp_path / "library"
    old, new = root / "Book.epub", root / "Author - Book.epub"
    if boundary == "after_cleanup":
        assert not old.exists() and new.is_file()
    else:
        assert old.samefile(new)
    with closing(Storage(tmp_path / "db.sqlite3")) as storage:
        placement, = storage.placements()
        entry, = storage.catalog_entries()
        named = root / name
        assert placement["path"] == entry["metadata"]["path"] == str(named)
        assert entry["placement_id"] == placement["placement_id"]
        assert placement["identity_id"] == "id1" and placement["source_item_id"] == "book-1"
        assert named.read_bytes() == b"book-data"
        assert placement["artifact_id"] == "sha256:" + sha256(named.read_bytes()).hexdigest()
        assert storage.acquisitions() == [] and storage.entitlements() == []
        assert storage.placement_for_path(named)["placement_id"] == placement["placement_id"]
        other = new if named == old else old
        assert storage.placement_for_path(other) is None


def crash_classification(root, boundary):
    """Actual common writer, with a finalized canonical provider checkpoint."""
    import json
    from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
    from cifereca.core import canonical_recovery as recovery
    from cifereca.core.canonical_publication import publish_completed_acquisition
    from cifereca.core.convergence_execution import CompletedDelivery
    from cifereca.locking import InterProcessLock
    from cifereca.plugin_api import LibraryMutationRequest
    from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register
    from tests.test_core_p0_3176_organizer_review import _put_epub, _service

    library = root / "library"
    storage = Storage(root / "db.sqlite3")
    register(storage, "A", "numilog", "card-A")
    source, snapshot = new_source(provider="numilog")
    old, pid = _put_epub(storage, library, "numilog/acsm-" + "a"*64 + "/legacy.epub")
    new = library / "A/numilog" / old.name
    digest = sha256(old.read_bytes()).hexdigest()
    lock_path = root / "acquisition.lock"
    recovery.acquisition_lock_file = lambda **_kw: lock_path
    with InterProcessLock(lock_path) as lease:
        recovery.record_completed_delivery_for_recovery(
            storage, source=source, offer_id="synthetic-offer",
            completed=CompletedDelivery(source, "synthetic-offer", old, digest, "synthetic-receipt"),
            library_root=library, shared_lock=lease)
        published = publish_completed_acquisition(
            storage, library_root=library, path=old, source=source,
            snapshot=snapshot, final_sha256=digest, delivery_receipt_ref="synthetic-receipt")
        assert published.placement_id == pid
        recovery.mark_completed_delivery_published(
            storage, source=source, offer_id="synthetic-offer",
            artifact_id=published.artifact_id, shared_lock=lease)
        with storage._lock, storage._connect() as db:
            db.execute("UPDATE placements SET metadata_json=? WHERE placement_id=?",
                       (json.dumps({"relative_path": str(old.relative_to(library))}), pid))
            # The precommit fixture must model an existing catalogue path,
            # not rely on the relocation itself to synthesize that field.
            db.execute("UPDATE catalog_entries SET metadata_json=? WHERE placement_id=?",
                       (json.dumps({"path": str(old)}), pid))
        broker = LibraryMaintenanceBrokerImpl(_service(storage, library))
        plan = broker.plan(LibraryMutationRequest("normalize_filename", placement_id=pid))
        relocate = storage.relocate_local_path
        if boundary == "before_commit":
            def interrupt(*args, **kwargs):
                def trace(statement):
                    if statement == "COMMIT":
                        os._exit(41)
                storage._connect().set_trace_callback(trace)
                relocate(*args, **kwargs)
            storage.relocate_local_path = interrupt
        elif boundary == "after_commit":
            def interrupt(*args, **kwargs):
                relocate(*args, **kwargs)
                os._exit(42)
            storage.relocate_local_path = interrupt
        else:
            original = Path.unlink

            def interrupt(path, *args, **kwargs):
                original(path, *args, **kwargs)
                if path == old:
                    os._exit(43)
            Path.unlink = interrupt
        broker._run_filename_relocation(plan, Control(), classified_target=new)
    raise AssertionError("classification did not reach the expected interruption")


@pytest.mark.parametrize("boundary,expected_exit", [
    ("before_commit", 41), ("after_commit", 42), ("after_cleanup", 43),
])
def test_restart_classification_preserves_checkpoint_and_zero_get(tmp_path, monkeypatch,
                                                              boundary, expected_exit):
    from dataclasses import replace
    from cifereca.core import canonical_recovery as recovery
    from cifereca.locking import InterProcessLock
    from cifereca.models import Account
    from tests.test_core_p0_3171_atomic_publication_v14 import new_source, rows
    from tests.test_core_p0_3171_convergence_execution import FakeService, run

    repo = Path(__file__).resolve().parents[1]
    environment = {**os.environ,
                   "PYTHONPATH": os.pathsep.join((str(repo / "src"), *sys.path))}
    program = (
        "import sys; from pathlib import Path; "
        "from tests.test_core_p0_3176_rename_process_restart import crash_classification; "
        "crash_classification(Path(sys.argv[1]), sys.argv[2])"
    )
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path), boundary],
                            cwd=repo, env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == expected_exit, result.stderr
    library = tmp_path / "library"
    old = library / "A/numilog" / ("acsm-" + "a"*64) / "legacy.epub"
    new = library / "A/numilog/legacy.epub"
    named = old if boundary == "before_commit" else new
    other = new if named == old else old
    assert named.is_file()
    if boundary == "after_cleanup":
        assert not old.exists()
    else:
        assert old.samefile(new)
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda **_kw: lock_path)
    monkeypatch.setattr("cifereca.core.convergence_execution.acquisition_lock_file",
                        lambda: lock_path)
    source, snapshot = new_source(provider="numilog")
    offered = replace(snapshot, rights=(replace(snapshot.rights[0],
                      delivery="available", delivery_offer_id="synthetic-offer"),))
    with closing(Storage(tmp_path / "db.sqlite3")) as storage:
        placement, = storage.placements()
        entry, = storage.catalog_entries()
        assert placement["path"] == entry["metadata"]["path"] == str(named)
        assert placement["metadata"]["relative_path"] == str(named.relative_to(library))
        assert placement["provider_id"] is None and placement["source_item_id"] is None
        assert storage.placement_for_path(other) is None
        with InterProcessLock(lock_path) as lease:
            receipt = recovery.recover_completed_delivery(
                storage, source=source, offer_id="synthetic-offer",
                library_root=library, shared_lock=lease)
            assert receipt is not None and receipt.path == named
            assert receipt.final_sha256 == sha256(named.read_bytes()).hexdigest()
        account = Account(identity_id="A", provider_id="numilog", credential_id="card-A",
                          unique_id="buyer-A")
        service = FakeService(storage, [account])
        before = storage.placements(), storage.acquisitions(), rows(storage)
        physical = sorted((str(path), sha256(path.read_bytes()).hexdigest())
                          for path in library.rglob("*.epub"))
        result = run(service, account, offered, library, lock_path, {source},
                     lambda *_: pytest.fail("restart must not re-download final bytes"))
        assert result[0].status == "present_attested" and service.calls == 0
        assert (storage.placements(), storage.acquisitions(), rows(storage)) == before
        assert sorted((str(path), sha256(path.read_bytes()).hexdigest())
                      for path in library.rglob("*.epub")) == physical
        # Inventory detects the extra physical name. Explicit local indexing
        # restores path parity without synthesizing a new provider origin.
        from cifereca.catalog import LocalCatalog
        from cifereca.core.library_inventory import scan_identity_roots
        from cifereca.core import canonical_dedup_supervisor as supervisor

        def physical_paths():
            inv = scan_identity_roots({"A": (library / "A",)})
            assert not inv.reasons
            assert all(f.sha256 == receipt.final_sha256 for f in inv.files)
            return {f.path for f in inv.files}

        def present_paths():
            return {p["path"] for p in storage.placements() if p["state"] == "present"}

        if boundary != "after_cleanup":
            assert physical_paths() - present_paths() == {str(other)}
            with InterProcessLock(lock_path) as lease:
                indexed = LocalCatalog(storage, library).index_path(other, identity_id="A")
                assert indexed["status"] == "indexed"
                candidate = storage.placement_for_path(other)
                assert candidate["artifact_id"] == placement["artifact_id"]
                assert candidate["identity_id"] == "A"
                assert candidate["provider_id"] is None and candidate["source_item_id"] is None
                assert physical_paths() == present_paths() == {str(old), str(new)}
                assert rows(storage) == (1, 2, 1, 1)
                kwargs = dict(library_root=library, candidate_placement_id=candidate["placement_id"],
                              keeper_placement_id=placement["placement_id"], shared_lock=lease,
                              action="commit", confirmed=True)
                held = supervisor.supervise_canonical_duplicate(storage, **kwargs)
                assert held.reason == "ordinary_cleanup_suspended"
                assert old.samefile(new)
                # Only the isolated existing supervisor is admitted. Restore
                # the durable SQL choice, not the most attractive filename.
                with monkeypatch.context() as patch:
                    patch.setattr(supervisor, "_ordinary_cleanup_enabled", lambda: True)
                    done = supervisor.supervise_canonical_duplicate(storage, **kwargs)
                assert done.finished and done.removed_file, done.reason
                assert not other.exists() and named.is_file()
                assert physical_paths() == present_paths() == {str(named)}
                assert rows(storage) == (1, 1, 1, 1)
        else:
            assert physical_paths() == present_paths() == {str(named)}
        # If the process died before commit, replay the common writer only
        # after parity is restored, on the authoritative original path.
        if boundary == "before_commit":
            from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
            from cifereca.plugin_api import LibraryMutationRequest
            from tests.test_core_p0_3176_organizer_review import _service
            broker = LibraryMaintenanceBrokerImpl(_service(storage, library))
            with InterProcessLock(lock_path) as lease:
                plan = broker.plan(LibraryMutationRequest("normalize_filename",
                                                          placement_id=placement["placement_id"]))
                broker._run_filename_relocation(plan, Control(), classified_target=new)
        assert not old.exists() and new.is_file()
        assert physical_paths() == present_paths() == {str(new)}
        assert rows(storage) == (1, 1, 1, 1)
        with InterProcessLock(lock_path) as lease:
            final = recovery.recover_completed_delivery(
                storage, source=source, offer_id="synthetic-offer",
                library_root=library, shared_lock=lease)
            assert final is not None and final.path == new
            assert final.final_sha256 == receipt.final_sha256
        assert run(service, account, offered, library, lock_path, {source},
                   lambda *_: pytest.fail("reconciled interruption must not GET"))[0].status == "present_attested"
        assert service.calls == 0
        assert physical_paths() == present_paths() == {str(new)}
