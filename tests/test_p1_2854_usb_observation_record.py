"""#2854: an attested USB scan can record history but never copy or unlink.

Real Core SQLite, synthetic scan in place of a *real* Linux USB volume.
Scanner/mount-inode/UUID safety has its own tests; no network or device I/O.
"""
from __future__ import annotations

from hashlib import sha256

import pytest

from cifereca.core import device_usb_observation_record as recorded
from cifereca.core.device_usb_preview import UsbDeviceScan, UsbObservedFile
from cifereca.identities import Identity
from cifereca.storage import Storage


NOW = 1800000120
DATA = b"synthetic-local-book"
SHA = sha256(DATA).hexdigest()
ART = "sha256:" + SHA
TARGET = "Books/Cifereca/" + SHA + ".epub"


@pytest.fixture
def rig(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    store.identity_put(Identity("alice", "Alice"))
    store.identity_put(Identity("bob", "Bob"))
    root = tmp_path / "alice"
    root.mkdir()
    file = root / "book.epub"
    file.write_bytes(DATA)
    store.artifact_put({
        "artifact_id": ART, "sha256": SHA, "size": len(DATA),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    store.placement_put({
        "artifact_id": ART, "path": str(file), "identity_id": "alice",
        "provider_id": "local", "source_item_id": "book", "state": "present",
        "metadata": {},
    })
    store.device_usb_binding_declare(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", label="Reader A", enabled=True,
    )
    store.device_usb_intent_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=ART, desired_state="keep",
    )
    store.device_usb_target_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=ART, target_relative_path=TARGET,
    )
    yield store, tmp_path / "fake-device", file
    store.close()


def scan(*, files=None, device="reader-a", identity="alice",
         fingerprint="uuid-alice-0001", when=NOW, complete=True):
    return UsbDeviceScan(
        identity, device, fingerprint, when, complete,
        {} if files is None else files,
    )


def observe(rig, monkeypatch, evidence):
    store, mount, _file = rig
    events = []
    def synthetic(**kwargs):
        events.append(kwargs)
        return evidence
    monkeypatch.setattr(recorded, "scan_managed_usb", synthetic)
    before = store.device_placement_get("reader-a", ART)
    status = recorded.record_verified_usb_observation(
        storage=store, identity_id="alice", device_id="reader-a",
        mountpoint=mount, now=NOW,
    )
    after = store.device_placement_get("reader-a", ART)
    assert len(events) == 1
    assert events[0]["identity_id"] == "alice"
    assert events[0]["binding"]["metadata"]["usb_fingerprint"] == "uuid-alice-0001"
    assert status.executable is False
    assert before["desired_state"] == after["desired_state"] == "keep"
    assert before["target_relative_path"] == after["target_relative_path"] == TARGET
    assert before["metadata"] == after["metadata"]
    return status, before, after


def test_present_is_physical_history_not_a_managed_receipt(rig, monkeypatch):
    s, before, after = observe(rig, monkeypatch, scan(files={
        ART: UsbObservedFile(TARGET, SHA, True),
    }))
    assert (s.status, s.updated, s.unresolved) == ("recorded", 1, 0)
    assert before["observed_state"] == "unknown"
    assert after["observed_state"] == "present"
    assert after["last_observed_at"] == NOW
    assert "managed_receipt" not in str(after["metadata"])
    assert rig[2].read_bytes() == DATA
    second, _, again = observe(rig, monkeypatch, scan(files={
        ART: UsbObservedFile(TARGET, SHA, True),
    }))
    assert (second.updated, second.unchanged) == (0, 1)
    assert again == after


def test_full_scan_proves_missing_without_removing_any_file(rig, monkeypatch):
    s, before, after = observe(rig, monkeypatch, scan())
    assert (s.status, s.updated, s.unresolved) == ("recorded", 1, 0)
    assert before["observed_state"] == "unknown"
    assert after["observed_state"] == "missing"
    assert after["last_observed_at"] == NOW
    assert rig[2].read_bytes() == DATA


@pytest.mark.parametrize("observed", [
    {ART: UsbObservedFile(TARGET, "f" * 64, True)},
    {"untracked:foreign": UsbObservedFile(TARGET, SHA, True)},
    {ART: UsbObservedFile(TARGET + "x", SHA, True)},
    {"untracked:moved": UsbObservedFile("Books/Cifereca/moved.epub", SHA, True)},
])
def test_mismatch_or_occupied_path_does_not_publish_missing_or_present(
    rig, monkeypatch, observed,
):
    outcome, before, after = observe(rig, monkeypatch, scan(files=observed))
    assert (outcome.status, outcome.updated, outcome.unresolved) == (
        "recorded", 0, 1,
    )
    assert before == after


@pytest.mark.parametrize("evidence", [
    None,
    scan(identity="bob"), scan(device="reader-b"),
    scan(fingerprint="uuid-foreign-0001"),
    scan(when=NOW - 301), scan(when=NOW + 1),
    scan(complete=False),
])
def test_invalid_device_scan_does_not_update_sqlite(rig, monkeypatch, evidence):
    outcome, before, after = observe(rig, monkeypatch, evidence)
    assert outcome.status == "blocked"
    assert outcome.reason == "usb_scan_unverified"
    assert before == after


@pytest.mark.parametrize("change", [
    "desired", "disabled", "fingerprint", "relocated", "foreign", "same_value_rewrite",
])
def test_scope_rebind_during_scan_refuses_commit(rig, monkeypatch, change):
    store, mount, file = rig
    initial = store.device_placement_get("reader-a", ART)
    def altered(**kwargs):
        if change == "desired":
            store.device_usb_intent_set(
                identity_id="alice", device_id="reader-a",
                artifact_id=ART, desired_state="remove",
            )
        elif change == "disabled":
            store.device_binding_put({
                **store.device_binding_get("reader-a"), "enabled": False,
            })
        elif change == "fingerprint":
            row = store.device_binding_get("reader-a")
            store.device_binding_put({
                **row, "metadata": {"usb_fingerprint": "uuid-alice-0002"},
            })
        elif change == "relocated":
            store.device_placement_put({
                **initial, "target_relative_path": "Books/Cifereca/other.epub",
            })
        elif change == "foreign":
            store.device_binding_put({
                **store.device_binding_get("reader-a"), "identity_id": "bob",
            })
        else:
            # A→B→A in the same second still changes the connection counter.
            store.device_usb_intent_set(
                identity_id="alice", device_id="reader-a",
                artifact_id=ART, desired_state="remove",
            )
            store.device_usb_intent_set(
                identity_id="alice", device_id="reader-a",
                artifact_id=ART, desired_state="keep",
            )
        return scan(files={ART: UsbObservedFile(TARGET, SHA, True)})
    monkeypatch.setattr(recorded, "scan_managed_usb", altered)
    out = recorded.record_verified_usb_observation(
        storage=store, identity_id="alice", device_id="reader-a",
        mountpoint=mount, now=NOW,
    )
    assert out.status == "blocked"
    assert store.device_placement_get("reader-a", ART)["observed_state"] == "unknown"
    assert file.read_bytes() == DATA


def test_foreign_profile_and_invalid_scope_do_not_scan(rig, monkeypatch):
    store, mount, file = rig
    monkeypatch.setattr(
        recorded, "scan_managed_usb",
        lambda **kwargs: pytest.fail("foreign/invalid scope cannot scan"),
    )
    assert recorded.record_verified_usb_observation(
        storage=store, identity_id="bob", device_id="reader-a",
        mountpoint=mount, now=NOW,
    ).status == "blocked"
    assert recorded.record_verified_usb_observation(
        storage=store, identity_id="alice", device_id="reader-a",
        mountpoint=mount, now=0,
    ).status == "blocked"
    assert file.read_bytes() == DATA


def test_corrupt_intent_owner_or_scan_failure_preserves_old_state(
    rig, monkeypatch,
):
    store, mount, _ = rig
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE device_placements SET metadata_json='{}' WHERE device_id=?",
            ("reader-a",),
        )
    monkeypatch.setattr(
        recorded, "scan_managed_usb",
        lambda **kwargs: pytest.fail("unowned intent cannot scan"),
    )
    assert recorded.record_verified_usb_observation(
        storage=store, identity_id="alice", device_id="reader-a",
        mountpoint=mount, now=NOW,
    ).status == "blocked"


def test_persisted_presence_is_only_last_known_without_new_usb_scan(
    rig, monkeypatch,
):
    from cifereca.core.device_usb_preview import preview_usb_delta

    store, mount, file = rig
    monkeypatch.setattr(
        recorded, "scan_managed_usb",
        lambda **_: scan(files={ART: UsbObservedFile(TARGET, SHA, True)}),
    )
    first = recorded.record_verified_usb_observation(
        storage=store, identity_id="alice", device_id="reader-a",
        mountpoint=mount, now=NOW,
    )
    assert first.status == "recorded"
    rows = preview_usb_delta(
        identity_id="alice", binding=store.device_binding_get("reader-a"),
        placements=store.device_placements(device_id="reader-a"),
        local={}, scan=None, now=NOW + 1,
    )
    assert len(rows) == 1
    assert (rows[0].last_known, rows[0].observed, rows[0].action) == (
        "present", "unknown", "verify",
    )
    assert rows[0].executable is False
    assert file.read_bytes() == DATA
