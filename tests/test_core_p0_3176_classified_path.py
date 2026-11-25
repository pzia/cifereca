"""#3176: classified destination is layout advice only, never a relocation."""
import pytest

from cifereca.gui.library_organizer_line import _display
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, rows
from tests.test_core_p0_3176_organizer_review import _put_epub, _service, _run
from tests.test_core_p0_4030_epub_evidence import META

TYPED = META + '<dc:type xmlns:dc="http://purl.org/dc/elements/1.1/">https://schema.org/Book</dc:type>'


@pytest.mark.parametrize("state,reason,expected", [
    ("missing", "classified_directory_missing", True),
    ("available", "classified_layout_advice_only", True),
    ("file", "classified_target_occupied", False),
    ("sql", "classified_target_occupied", False),
    ("alias", "classified_target_unverified", False),
    ("nondirectory", "classified_target_unverified", False),
    ("history", "classified_source_not_admitted", False),
    ("unclassified", "classified_evidence_unavailable", False),
    ("duplicate", None, False),
])
def test_classified_path_only_for_unique_verified_acquisition(
    store, tmp_path, state, reason, expected,
):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    source, pid = _put_epub(
        store, root, "shared/old.epub" if state == "history" else "Acquisitions/old.epub",
        metadata=META if state == "unclassified" else TYPED,
    )
    target = root / "A" / "Livres" / "Autrice" / "Autrice - Roman commun.epub"
    if state in ("available", "file"):
        target.parent.mkdir(parents=True)
        if state == "file":
            target.write_bytes(b"foreign bytes")
    elif state == "sql":
        # A missing path is still reserved by an operational Placement.
        store.placement_put({"identity_id": "A", "state": "missing", "path": str(target)})
    elif state == "alias":
        external = tmp_path / "external"
        external.mkdir()
        (root / "A" / "Livres").symlink_to(external, target_is_directory=True)
    elif state == "nondirectory":
        (root / "A" / "Livres").write_bytes(b"not a directory")
    elif state == "duplicate":
        duplicate = source.with_name("copy.epub")
        duplicate.write_bytes(source.read_bytes())
        store.placement_put({"identity_id": "A", "state": "present",
                             "artifact_id": store.placement_get(pid)["artifact_id"],
                             "path": str(duplicate)})
    before = rows(store)
    tree = sorted(str(p) for p in root.rglob("*"))
    original = source.read_bytes()
    _, result = _run(_service(store, root))
    file = next(f for group in result["groups"] for f in group["files"] if f["placement"] == pid)
    assert file["classified_path_reason"] == reason
    proposed = "Livres/Autrice/Autrice - Roman commun.epub"
    assert file["classified_path_advice"] == (proposed if expected else None)
    assert (proposed in _display(result)) is expected
    assert not any(group["relocation_proposed"] for group in result["groups"])
    assert rows(store) == before and tree == sorted(str(p) for p in root.rglob("*"))
    assert source.read_bytes() == original
    if state == "file":
        assert target.read_bytes() == b"foreign bytes"


@pytest.mark.parametrize("dependency", ["none", "relative_path", "checkpoint"])
def test_classified_target_existing_sqlite_relocation_boundary_isolated(
    store, tmp_path, dependency,
):
    """Qualify only the existing SQL writer, not a classification command.

    Test setup stages a hardlink on the same filesystem. Both names remain
    throughout: no production executor, cleanup, or relocation authority.
    """
    import json
    import os
    from cifereca.core.library_organizer_plan import (
        prepare_organizer_dry_run, confirm_organizer_dry_run,
    )

    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    old, pid = _put_epub(store, root, "Acquisitions/Autrice - Roman commun.epub",
                         metadata=TYPED)
    scope, page = _run(_service(store, root))
    plan = prepare_organizer_dry_run(scope, page, confirmed=True)
    accepted = confirm_organizer_dry_run(scope, page, plan, confirmed=True)
    assert accepted.confirmed and not accepted.executable
    entry, = (e for e in accepted.entries if e.operation == "classify")
    assert entry.placement_id == pid and entry.state == "relocation_held"
    target = root / "A" / entry.proposed
    assert target == root / "A/Livres/Autrice/Autrice - Roman commun.epub"
    # Separate synthetic SQL qualification: bind the fixture explicitly.
    # This invalidates the held plan; it is never a writer authorization.
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET provider_id=?,source_item_id=? WHERE placement_id=?",
                   ("local", "fixture-item", pid))
        db.execute("UPDATE catalog_entries SET provider_id=?,item_id=? WHERE placement_id=?",
                   ("local", "fixture-item", pid))
    assert confirm_organizer_dry_run(scope, page, plan, confirmed=True) is None
    if dependency == "relative_path":
        with store._lock, store._connect() as db:
            db.execute("UPDATE placements SET metadata_json=? WHERE placement_id=?",
                       (json.dumps({"relative_path": "Acquisitions/Autrice - Roman commun.epub"}), pid))
    elif dependency == "checkpoint":
        store.acquisition_put({
            "provider_id": "local", "credential_id": "fixture-account",
            "identity_id": "A", "item_id": "fixture-checkpoint",
            "status": "attention_required",
            "metadata": {"phase": "index_pending", "path": str(old)},
        })
    baseline, data = store.placement_get(pid), old.read_bytes()
    before = rows(store)
    history = store.acquisitions()
    # Fixture-only staging: the production organizer has no such command.
    target.parent.mkdir(parents=True)
    os.link(old, target)
    if dependency == "none":
        store.relocate_local_path(old, target, identity_id="A", provider_id="local")
        current = store.placement_get(pid)
        assert current["path"] == str(target)
        for key in ("artifact_id", "identity_id", "provider_id", "source_item_id", "state"):
            assert current[key] == baseline[key]
        assert store.placement_for_path(old) is None
        assert all(e["metadata"]["path"] == str(target)
                   for e in store.catalog_entries(placement_id=pid))
    else:
        code = ("relocate_relative_path_requires_relink" if dependency == "relative_path"
                else "relocate_acquisition_reference_requires_relink")
        with pytest.raises(ValueError, match=code):
            store.relocate_local_path(old, target, identity_id="A", provider_id="local")
        assert store.placement_get(pid) == baseline
        assert store.placement_for_path(target) is None
    assert old.samefile(target) and old.read_bytes() == target.read_bytes() == data
    assert rows(store) == before and store.acquisitions() == history
    assert store.entitlements() == []


@pytest.mark.parametrize("case", ["success", "production_hold", "checkpoint", "collision", "sql_failure",
                                  "acsm_success", "acsm_staged", "missing_directory", "missing_directory_hold",
                                  "acsm_reconstruct"])
def test_native_classification_uses_the_common_writer_and_keeps_authority(
    store, tmp_path, monkeypatch, case,
):
    """One consent → actual activation → Core writer → FS/SQLite receipt.

    The production fence is deliberately closed. Only the synthetic case
    admits the exact temporary library under the shared acquisition lock.
    """
    import json
    from types import SimpleNamespace as NS
    from cifereca.core import canonical_recovery, library_organizer_execution
    from cifereca.core.activation_scoped_broker import ActivationBrokerResolver
    from cifereca.core.maintenance_read_ports import maintenance_read_ports
    from cifereca.gui.organizer_native_confirmation import NativeOrganizerConfirmation
    from cifereca.locking import InterProcessLock
    from tests.test_library_declaration_actions import Widget, Dialog, Runner

    root = tmp_path / "library"
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(canonical_recovery, "acquisition_lock_file", lambda **_kw: lock_path)
    with InterProcessLock(lock_path):
        pass
    acsm = case.startswith("acsm_")
    if acsm:
        import hashlib
        from cifereca.core.canonical_publication import publish_completed_acquisition
        from cifereca.core.convergence_execution import CompletedDelivery
        from tests.test_core_p0_3171_atomic_publication_v14 import new_source
        register(store, "A", "numilog", "card-A")
        source, snapshot = new_source(provider="numilog")
        old, pid = _put_epub(store, root, "numilog/acsm-" + "a"*64 + "/legacy.epub",
                             metadata=TYPED)
        digest = hashlib.sha256(old.read_bytes()).hexdigest()
        offer = "synthetic-offer"
        with InterProcessLock(lock_path) as lock:
            receipt = canonical_recovery.record_completed_delivery_for_recovery(
                store, source=source, offer_id=offer,
                completed=CompletedDelivery(source, offer, old, digest, "synthetic-receipt",
                                            adept_receipt_ref="synthetic-adept"),
                library_root=root, shared_lock=lock)
            published = publish_completed_acquisition(
                store, library_root=root, path=old, source=source, snapshot=snapshot,
                final_sha256=digest, delivery_receipt_ref="synthetic-receipt",
                adept_receipt_ref="synthetic-adept")
            assert published.placement_id == pid
            if case in ("acsm_success", "acsm_reconstruct"):
                canonical_recovery.mark_completed_delivery_published(
                    store, source=source, offer_id=offer, artifact_id=published.artifact_id,
                    shared_lock=lock)
        target = root / "A/numilog" / old.name
    else:
        register(store, "A", "seller", "card-A")
        old, pid = _put_epub(store, root, "Acquisitions/Autrice - Roman commun.epub",
                             metadata=TYPED, provider_id="local")
        target = root / "A/Livres/Autrice" / old.name
        if case not in ("missing_directory", "missing_directory_hold"):
            target.parent.mkdir(parents=True)
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET metadata_json=? WHERE placement_id=?",
                   (json.dumps({"relative_path": str(old.relative_to(root)),
                                "artifact_kind": "file"}), pid))
    if case == "checkpoint":
        store.acquisition_put({
            "provider_id": "local", "credential_id": "fixture-account",
            "identity_id": "A", "item_id": "fixture-checkpoint",
            "status": "attention_required",
            "metadata": {"phase": "index_pending", "path": str(old)},
        })
    baseline, data, history = store.placement_get(pid), old.read_bytes(), store.acquisitions()
    before = rows(store)
    service = _service(store, root)
    if case == "acsm_staged":
        _, review = _run(service)
        file, = review["groups"][0]["files"]
        assert file["classified_path_advice"] is None
        assert old.read_bytes() == data and not target.exists()
        assert store.placement_get(pid) == baseline and store.acquisitions() == history
        return
    key = "library.organizer.read"
    resolver = ActivationBrokerResolver(lambda _: None, maintenance_read_ports(service).get,
                                        "organizer", (key,))
    broker = resolver(key)
    plugin = object()
    resolved = NS(plugin_id="organizer", service=plugin, registry_generation=1)
    manager = NS(_registry_generation=1, service=lambda _: plugin,
                 _records={"organizer": NS(service=plugin, activation_brokers=resolver)})
    page = broker.preview(identity_id="A", confirmed=True)
    draft = broker.prepare_dry_run(token=page.token, confirmed=True)
    if case == "checkpoint" and draft is None:
        # A dependency can already prevent a preview. Never manufacture a
        # plan just to reach the writer.
        assert old.read_bytes() == data and store.placement_get(pid) == baseline
        assert store.acquisitions() == history and not target.exists()
        resolver.close()
        return
    page = broker.confirm_dry_run(token=draft.token, confirmed=True)
    entry, = page.dry_run_entries
    assert entry.operation == "classify" and entry.state == "relocation_held"
    assert entry.proposed == str(target.relative_to(root / "A"))
    if case not in ("production_hold", "missing_directory_hold"):
        monkeypatch.setattr(library_organizer_execution, "_execution_fence_current",
                            lambda scope, lock: scope.root == root and scope.storage is store)
    if case == "collision":
        target.write_bytes(b"foreign")
    elif case == "sql_failure":
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic SQLite failure")
        monkeypatch.setattr(store, "relocate_local_path", fail)

    messages = []
    runner = Runner()
    runner.deferred = True
    Dialog.dialogs = []
    window = NS(service=service, plugin_manager=manager, runner=runner, toast=messages.append)
    lease = NS(identity_id="A", is_current=lambda: True)
    ui = NativeOrganizerConfirmation(window, resolved, lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert ui.open(NS(MessageDialog=Dialog))
    dialog, = Dialog.dialogs
    assert "rangement" in dialog.label
    assert entry.current in dialog.body and entry.proposed in dialog.body
    assert old.read_bytes() == data and store.placement_get(pid) == baseline
    dialog.respond("apply")
    assert len(runner.queue) == 1
    runner.run()
    assert not ui.open(NS(MessageDialog=Dialog))
    if case in ("success", "acsm_success", "missing_directory", "acsm_reconstruct"):
        assert messages == ["Rangement confirmé. Relancez la revue locale."]
        assert not old.exists() and target.read_bytes() == data
        current = store.placement_get(pid)
        assert current["path"] == str(target)
        assert current["metadata"]["relative_path"] == str(target.relative_to(root))
        assert current["metadata"]["artifact_kind"] == "file"
        for field in ("artifact_id", "identity_id", "provider_id", "source_item_id", "state"):
            assert current[field] == baseline[field]
        assert store.placement_for_path(old) is None
        assert all(e["metadata"]["path"] == str(target)
                   for e in store.catalog_entries(placement_id=pid))
    else:
        assert messages and "confirmé" not in messages[0]
        assert old.read_bytes() == data and store.placement_get(pid) == baseline
        if case == "collision":
            assert target.read_bytes() == b"foreign"
        else:
            assert not target.exists()
        if case == "missing_directory_hold":
            assert not target.parent.exists()
    assert rows(store) == before
    if case in ("acsm_success", "acsm_reconstruct"):
        from dataclasses import replace
        from cifereca.models import Account
        from tests.test_core_p0_3171_convergence_execution import FakeService, run
        with InterProcessLock(lock_path) as lock:
            recovered = canonical_recovery.recover_completed_delivery(
                store, source=source, offer_id=offer, library_root=root, shared_lock=lock)
            assert recovered is not None and recovered.path == target
            assert recovered.final_sha256 == digest and recovered.adept_receipt_ref == "synthetic-adept"
        account = Account(identity_id="A", provider_id="numilog", credential_id="card-A",
                          unique_id="buyer-A")
        sync = FakeService(store, [account])
        offered = replace(snapshot, rights=(replace(snapshot.rights[0],
                          delivery="available", delivery_offer_id=offer),))
        monkeypatch.setattr("cifereca.core.convergence_execution.acquisition_lock_file",
                            lambda: lock_path)
        results = run(sync, account, offered, root, lock_path, {source},
                      lambda *_: pytest.fail("relocated attested EPUB must avoid GET"))
        assert results[0].status == "present_attested"
        assert not old.exists() and target.read_bytes() == data
        assert rows(store) == before and sync.calls == 0
        if case == "acsm_reconstruct":
            _receive_reconstructible_link(store, tmp_path, root, lock_path, monkeypatch,
                                          source, snapshot, account, sync, pid, target, data, digest)
    else:
        assert store.acquisitions() == history
    assert store.entitlements() == []
    resolver.close()


def _receive_reconstructible_link(store, tmp_path, root, lock_path, monkeypatch,
                                 source, snapshot, account, sync, keeper, target, data, digest):
    """Owner #3176: qualify recovery of a deliberately lost reproducible link.

    The only injected defect is removal of the exact synthetic provider link.
    No production guard or deletion policy is changed. A fresh provider offer
    returns identical bytes and the same reproducible receipt; the old ADEPT
    proof and the old completed journal are retained.
    """
    from dataclasses import replace
    from cifereca.core import canonical_dedup_supervisor as supervisor
    from cifereca.core.convergence_execution import CompletedDelivery
    from cifereca.core.convergence_verified_presence import plan_existing_verified_presence
    from cifereca.core.library_inventory import scan_identity_roots
    from cifereca.core.library_reconciliation import read_library_snapshot, reconcile
    from cifereca.locking import InterProcessLock
    from tests.test_core_p0_3171_convergence_execution import run

    def inventory_report(expected_paths):
        inventory = scan_identity_roots({"A": (root / "A",)})
        assert not inventory.reasons
        assert {f.path for f in inventory.files} == {str(p) for p in expected_paths}
        assert all(f.sha256 == digest for f in inventory.files)
        assert {p["path"] for p in store.placements() if p["state"] == "present"} == {
            f.path for f in inventory.files}
        report = reconcile(inventory, read_library_snapshot(tmp_path / "state.sqlite",
                                                            identity_id="A"))
        assert not report.reasons and not report.missing_placements
        return report

    assert "confirmed_link" in inventory_report([target]).files[0]
    with store._lock, store._connect() as db:
        original = db.execute("SELECT * FROM origin_links ORDER BY origin_link_id").fetchall()
        removed = db.execute(
            "DELETE FROM origin_links WHERE origin_kind='provider' AND identity_id=? "
            "AND artifact_id=? AND evidence_ref=?",
            ("A", "sha256:" + digest, "synthetic-receipt"))
        assert removed.rowcount == 1
    assert "present_unlinked" in inventory_report([target]).files[0]
    assert target.read_bytes() == data
    offered = replace(snapshot, rights=(replace(snapshot.rights[0],
                      delivery="available", delivery_offer_id="fresh-recovery-offer"),))
    assert not plan_existing_verified_presence(store, offered, library_root=root).decisions[0].provider_satisfied
    download = root / "A/numilog/reacquired.epub"

    def deliver(key, right):
        assert key == source and right.delivery_offer_id == "fresh-recovery-offer"
        assert not download.exists()
        sync.calls += 1
        download.write_bytes(data)
        return CompletedDelivery(key, right.delivery_offer_id, download, digest,
                                 "synthetic-receipt", adept_receipt_ref="synthetic-adept")

    outcome, = run(sync, account, offered, root, lock_path, {source}, deliver)
    assert outcome.status == "downloaded" and sync.calls == 1
    assert outcome.artifact_id == "sha256:" + digest
    candidate = outcome.placement_id
    assert candidate != keeper and target.read_bytes() == download.read_bytes() == data
    assert store.placement_get(candidate)["path"] == str(download)
    inventory_report([target, download])
    with store._lock, store._connect() as db:
        # Reproducing the exact receipt restores the original origin IDs.
        # Timestamps are not evidence; compare the immutable coordinates.
        columns = "origin_link_id,origin_kind,identity_id,artifact_id,source_reference_id,evidence_ref"
        restored = db.execute("SELECT " + columns + " FROM origin_links ORDER BY origin_link_id").fetchall()
        assert len(restored) == len(original) == 2
        assert {r[1] for r in restored} == {"provider", "adept"}
    with InterProcessLock(lock_path) as lock:
        kwargs = dict(library_root=root, candidate_placement_id=candidate,
                      keeper_placement_id=keeper, action="commit", confirmed=True,
                      shared_lock=lock)
        held = supervisor.supervise_canonical_duplicate(store, **kwargs)
        assert held.reason == "ordinary_cleanup_suspended"
        assert target.read_bytes() == download.read_bytes() == data
        # Reuse the existing supervisor test admission, confined to this
        # temporary library. Never enable production ordinary cleanup.
        with monkeypatch.context() as patch:
            patch.setattr(supervisor, "_ordinary_cleanup_enabled", lambda: True)
            done = supervisor.supervise_canonical_duplicate(store, **kwargs)
        assert done.finished and done.removed_file, done.reason
        assert done.receipt_relinked == 1
    assert not download.exists() and target.read_bytes() == data
    assert store.placement_get(candidate) is None
    assert "confirmed_link" in inventory_report([target]).files[0]
    assert all(e["placement_id"] == keeper for e in store.catalog_entries())
    with store._lock, store._connect() as db:
        assert db.execute("SELECT " + columns + " FROM origin_links ORDER BY origin_link_id").fetchall() == restored
    result, = run(sync, account, offered, root, lock_path, {source},
                  lambda *_: pytest.fail("reconciled keeper must prevent another GET"))
    assert result.status == "present_attested" and sync.calls == 1
    assert not download.exists() and target.read_bytes() == data
