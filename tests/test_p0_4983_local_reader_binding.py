"""Real Linux directory identity and transactional pseudo-reader binding."""
from pathlib import Path
import pytest
from cifereca.identities import Identity
from cifereca.storage import Storage
from cifereca.core.device_usb_mount import observe_linux_usb_mount


def test_local_directory_binding_is_exclusive_and_replacement_refused(tmp_path):
    db = Storage(tmp_path / "library.sqlite3")
    for owner in ("alice", "bob"):
        db.identity_put(Identity(owner, owner))
    root = tmp_path / "reader"
    root.mkdir()
    args = dict(identity_id="alice", device_id="local-a", label="Local",
                root=root, enabled=True)
    assert db.device_local_reader_declare(**args) == "local-a"
    binding = db.device_binding_get("local-a")
    proof = observe_linux_usb_mount(identity_id="alice", binding=binding, mountpoint=root)
    assert proof is not None
    assert proof.filesystem.startswith("local:")
    assert observe_linux_usb_mount(identity_id="bob", binding=binding, mountpoint=root) is None
    with pytest.raises(ValueError):
        db.device_local_reader_declare(**(args | {"identity_id": "bob", "device_id": "local-b"}))
    nested = root / "nested"
    nested.mkdir()
    with pytest.raises(ValueError):
        db.device_local_reader_declare(**(args | {"root": nested, "device_id": "local-c"}))
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        db.device_local_reader_declare(**(args | {"root": alias, "device_id": "local-d"}))
    root.rename(tmp_path / "original")
    root.mkdir()
    assert observe_linux_usb_mount(identity_id="alice", binding=binding, mountpoint=root) is None
    with pytest.raises(ValueError):
        db.device_local_reader_declare(**args)


def test_pseudo_reader_real_copy_observe_remove_without_provider(tmp_path, monkeypatch):
    from hashlib import sha256
    from time import time
    from types import SimpleNamespace as NS
    import runpy
    from cifereca.core import device_usb_execution_broker as broker
    from cifereca.core.device_usb_copy_admission import UsbCopyConsent
    from cifereca.core.device_usb_removal_review import UsbRemovalConsent
    from cifereca.core.device_usb_sync_engine import UsbSyncResult
    from cifereca.core.device_usb_operation_journal import SqliteUsbOperationJournal
    from cifereca.plugin_api import USB_STAGE_CAPABILITY
    from tests.test_p1_2854_usb_dry_run_coordinator import epub_bytes
    import cifereca.paths as paths

    db = Storage(tmp_path / "library.sqlite3")
    db.identity_put(Identity("alice", "Alice"))
    db.identity_put(Identity("bob", "Bob"))
    library = tmp_path / "alice"
    library.mkdir()
    source = library / "book.epub"
    payload = epub_bytes()
    source.write_bytes(payload)
    digest = sha256(payload).hexdigest()
    artifact = "sha256:" + digest
    db.artifact_put(dict(artifact_id=artifact, sha256=digest, size=len(payload),
                        media_format="epub", metadata={"artifact_kind": "file"}))
    db.placement_put(dict(artifact_id=artifact, path=str(source), identity_id="alice",
                          provider_id="local", source_item_id="imported", state="present",
                          metadata={}))
    root = tmp_path / "reader"
    managed = root / "Books" / "Cifereca"
    managed.mkdir(parents=True)
    db.device_local_reader_declare(identity_id="alice", device_id="local-a",
                                   label="Local", root=root, enabled=True)
    binding = db.device_binding_get("local-a")
    fingerprint = binding["metadata"]["usb_fingerprint"]
    db.device_usb_reader_formats_set(identity_id="alice", device_id="local-a",
                                     usb_fingerprint=fingerprint, supported_formats=("epub",))
    db.device_usb_intent_set(identity_id="alice", device_id="local-a",
                             artifact_id=artifact, desired_state="keep")
    target = "Books/Cifereca/" + digest + ".epub"
    db.device_usb_target_set(identity_id="alice", device_id="local-a",
                             artifact_id=artifact, target_relative_path=target)
    third_party = managed / "third-party.txt"
    third_party.write_bytes(b"untouched")
    # Actual production storage.target service; no fake USB/mount or policy.
    plugin_file = Path(__file__).resolve().parents[1] / "src/plugins/storage-usb/cifereca_storage_usb.py"
    transport = runpy.run_path(str(plugin_file))["UsbMassStorageTarget"]()
    entry = NS(plugin_id="storage.usb", registry_generation=5,
               capabilities=(USB_STAGE_CAPABILITY,), service=transport)
    manager = NS(registry_generation=5, services=lambda _: (entry,))
    service = NS(current_identity=NS(identity_id="alice"), _account_context_epoch=3,
                 storage=db, config=NS(identities=lambda: [{"id": "alice"}, {"id": "bob"}]),
                 _plugin_manager=manager)
    monkeypatch.setattr(broker, "configured_identity_roots",
                        lambda _, owner: {owner: (tmp_path / owner,)})
    monkeypatch.setattr(paths, "state_dir", lambda **_: tmp_path / "state")
    facade = broker.ReaderUsbExecutionBrokerImpl(service)
    now = int(time())
    consent = UsbCopyConsent("alice", "local-a", fingerprint, artifact, digest,
                             "epub", target, "a" * 32, now, now + 240)
    proof = observe_linux_usb_mount(identity_id="alice",
                                    binding=db.device_binding_get("local-a"), mountpoint=root)
    assert proof is not None
    mount_key = proof.scope_key()[2:]
    worker = facade.admit_delta(identity_id="alice", device_id="local-a",
                                confirmations=(consent,), mountpoint=root,
                                expected_profile_epoch=3, mount_key=mount_key)
    final = root / target
    assert not final.exists()
    result = worker()
    assert result == UsbSyncResult("completed", copied=1, observed=True)
    assert worker().status == "blocked"
    assert final.read_bytes() == source.read_bytes() == payload
    journal = SqliteUsbOperationJournal(db)
    assert journal.get(consent.operation_id).rights is None
    assert db.device_placement_get("local-a", artifact)["observed_state"] == "present"
    assert facade.preview(identity_id="alice", device_id="local-a", mountpoint=root).rows[0].action == "skip"
    db.device_usb_intent_set(identity_id="alice", device_id="local-a",
                             artifact_id=artifact, desired_state="remove")
    removal = UsbRemovalConsent("alice", "local-a", fingerprint, artifact, digest, target,
                                consent.operation_id, "b" * 32, now, now + 240)
    worker = facade.admit_delta(identity_id="alice", device_id="local-a",
                                confirmations=(removal,), mountpoint=root,
                                expected_profile_epoch=3, mount_key=mount_key)
    assert worker() == UsbSyncResult("completed", removed=1, observed=True)
    assert not final.exists()
    assert source.read_bytes() == payload and third_party.read_bytes() == b"untouched"
