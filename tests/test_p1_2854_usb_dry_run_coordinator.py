"""P1 #2854: a dry-run can observe but NEVER authorize USB side effects.

Integration with real SQLite and real local file SHA, USB scan mocked by a
synthetic complete observation. No hardware mounted or network contacted.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from cifereca.core import device_usb_dry_run as run
from cifereca.core.device_usb_preview import UsbDeviceScan, UsbObservedFile
from cifereca.identities import Identity
from cifereca.storage import Storage


PAYLOAD = b"synthetic EPUB for local reader dry run"
DIGEST = sha256(PAYLOAD).hexdigest()
ARTIFACT = "sha256:" + DIGEST
TARGET = "Books/Cifereca/verified.epub"
NOW = 1800000120


def setup(tmp_path: Path, *, enabled=True, desired="keep", target=True):
    db = Storage(tmp_path / "library.sqlite3")
    db.identity_put(Identity("alice", "Alice"))
    db.identity_put(Identity("bob", "Bob"))
    root = tmp_path / "alice"
    root.mkdir()
    book = root / "test.epub"
    book.write_bytes(PAYLOAD)
    db.artifact_put({
        "artifact_id": ARTIFACT, "sha256": DIGEST, "size": len(PAYLOAD),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    db.placement_put({
        "artifact_id": ARTIFACT, "path": str(book), "identity_id": "alice",
        "provider_id": "local", "source_item_id": "book-one",
        "state": "present", "metadata": {},
    })
    db.device_usb_binding_declare(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001",
        label="Reader A", enabled=enabled,
    )
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=ARTIFACT, desired_state="keep",
    )
    if target:
        db.device_usb_target_set(
            identity_id="alice", device_id="reader-a",
            artifact_id=ARTIFACT, target_relative_path=TARGET,
        )
    if desired == "remove":
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-a",
            artifact_id=ARTIFACT, desired_state="remove",
        )
    return db, root, book, tmp_path / "fake-mount"


def fake_scan(*, identity_id="alice", device_id="reader-a", files=None,
              fingerprint="uuid-alice-0001", observed_at=NOW):
    return UsbDeviceScan(
        identity_id=identity_id, device_id=device_id,
        fingerprint=fingerprint, observed_at=observed_at,
        complete=True, files={} if files is None else files,
    )


def inspect(db, root, mount):
    return run.inspect_usb_dry_run(
        storage=db, identity_id="alice", device_id="reader-a",
        identity_root=root, mountpoint=mount, now=NOW,
    )


def test_verified_owned_source_and_complete_scan_only_propose_upload(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    calls = []
    monkeypatch.setattr(
        run, "scan_managed_usb",
        lambda **kwargs: calls.append(kwargs) or fake_scan(),
    )
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.scan_attested is True
    assert result.observed_at == NOW
    assert result.executable is False
    assert len(result.rows) == 1
    assert (result.rows[0].action, result.rows[0].observed) == (
        "upload_candidate", "missing",
    )
    assert result.rows[0].executable is False
    assert calls[0]["binding"]["identity_id"] == "alice"
    assert calls[0]["mountpoint"] == mount
    assert db.device_placements() == before
    assert book.read_bytes() == PAYLOAD


def test_scanned_identical_bytes_are_skip_not_a_new_managed_receipt(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_:
        fake_scan(files={ARTIFACT: UsbObservedFile(
            relative_path=TARGET, sha256=DIGEST, regular_file=True,
        )}),
    )
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.rows[0].action == "skip"
    assert result.rows[0].observed == "present"
    assert result.rows[0].executable is False
    assert db.device_placements() == before
    assert book.read_bytes() == PAYLOAD


def test_remove_wish_is_blocked_without_managed_write_receipt(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path, desired="remove")
    db.device_placement_put({
        "device_id": "reader-a", "artifact_id": ARTIFACT,
        "desired_state": "remove", "observed_state": "present",
        "target_relative_path": TARGET, "last_observed_at": 1700000000,
        "metadata": {"intent_identity_id": "alice"},
    })
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_:
        fake_scan(files={ARTIFACT: UsbObservedFile(
            relative_path=TARGET, sha256=DIGEST, regular_file=True,
        )}),
    )
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.rows[0].action == "blocked"
    assert result.rows[0].reason == "unmanaged_or_changed_file"
    assert result.rows[0].observed == "present"
    assert result.rows[0].executable is False
    assert db.device_placements() == before
    assert book.read_bytes() == PAYLOAD


def test_inaccessible_volume_never_proves_missing_from_old_sqlite(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_placement_put({
        "device_id": "reader-a", "artifact_id": ARTIFACT,
        "desired_state": "keep", "observed_state": "present",
        "target_relative_path": TARGET, "last_observed_at": 1700000000,
        "metadata": {"intent_identity_id": "alice"},
    })
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: None)
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.status == "device_unverified"
    assert result.scan_attested is False
    assert result.observed_at is None
    assert result.rows[0].action == "verify"
    assert result.rows[0].observed == "unknown"
    assert result.rows[0].last_known == "present"
    assert db.device_placements() == before


def test_missing_local_book_is_never_qualified_as_upload(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    book.unlink()
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.rows[0].action == "blocked"
    assert result.rows[0].reason == "owned_source_unverified"
    assert not result.rows[0].executable


def test_undeclared_target_must_be_qualified_before_positively_missing(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path, target=False)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    result = inspect(db, root, mount)
    assert result.rows[0].observed == "unknown"
    assert result.rows[0].reason == "target_path_unqualified"
    assert result.rows[0].executable is False


def test_disabled_reader_cannot_invent_present_or_missing(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path, enabled=False)
    def forbid(**kwargs):
        raise AssertionError("disabled reader must not be scanned")
    monkeypatch.setattr(run, "scan_managed_usb", forbid)
    result = inspect(db, root, mount)
    assert result.status == "device_unverified"
    assert result.rows[0].action == "blocked"
    assert result.rows[0].observed == "unknown"


def test_unavailable_volume_never_hashes_local_epubs(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: None)
    monkeypatch.setattr(
        run, "attest_local_usb_source",
        lambda **_: pytest.fail("no local SHA work without complete USB scan"),
    )
    result = inspect(db, root, mount)
    assert result.status == "device_unverified"
    assert result.rows[0].observed == "unknown"
    assert result.rows[0].executable is False


@pytest.mark.parametrize("invalid_scan", [
    "foreign_profile", "wrong_device", "wrong_uuid", "stale",
    "future_timestamp", "incomplete",
])
def test_invalid_scanner_output_never_claims_current_presence(
    tmp_path, monkeypatch, invalid_scan,
):
    db, root, book, mount = setup(tmp_path)
    valid = fake_scan(files={ARTIFACT: UsbObservedFile(
        relative_path=TARGET, sha256=DIGEST, regular_file=True,
    )})
    changed = {
        "foreign_profile": replace(valid, identity_id="bob"),
        "wrong_device": replace(valid, device_id="reader-unknown"),
        "wrong_uuid": replace(valid, fingerprint="uuid-mismatched-0001"),
        "stale": replace(valid, observed_at=NOW - 301),
        "future_timestamp": replace(valid, observed_at=NOW + 1),
        "incomplete": replace(valid, complete=False),
    }[invalid_scan]
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: changed)
    monkeypatch.setattr(
        run, "attest_local_usb_source",
        lambda **_: pytest.fail("unverified USB scan must not hash local books"),
    )
    result = inspect(db, root, mount)
    assert result.status == "device_unverified"
    assert result.scan_attested is False
    assert result.observed_at is None
    assert result.executable is False
    assert len(result.rows) == 1
    assert result.rows[0].observed == "unknown"
    assert result.rows[0].action == "verify"


def test_exact_identity_scope_blocks_cross_profile_without_ever_scanning(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    def forbid(**kwargs):
        raise AssertionError("foreign reader must not be scanned")
    monkeypatch.setattr(run, "scan_managed_usb", forbid)
    result = run.inspect_usb_dry_run(
        storage=db, identity_id="bob", device_id="reader-a",
        identity_root=tmp_path / "bob", mountpoint=mount, now=NOW,
    )
    assert result.status == "scope_unverified"
    assert result.rows == ()
    assert result.executable is False


def test_legacy_foreign_intent_must_not_expose_other_owners_artifact(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_placement_put({
        "device_id": "reader-a", "artifact_id": ARTIFACT,
        "desired_state": "keep", "observed_state": "present",
        "target_relative_path": TARGET, "last_observed_at": 1700000000,
        "metadata": {"intent_identity_id": "bob"},
    })
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_:
                        pytest.fail("legacy foreign scope should stop before scan"))
    result = inspect(db, root, mount)
    assert result.status == "scope_unverified"
    assert result.rows == ()
    assert ARTIFACT not in repr(result)


@pytest.mark.parametrize("change", ["device_changed", "desired_changed", "target_changed"])
def test_mid_scan_rebind_or_intent_mutation_discards_all_rows(tmp_path, monkeypatch, change):
    db, root, book, mount = setup(tmp_path)
    def altered(**kwargs):
        if change == "device_changed":
            db.device_binding_put({
                **db.device_binding_get("reader-a"),
                "identity_id": "bob",
                "metadata": {"usb_fingerprint": "uuid-bob-9000"},
            })
        elif change == "desired_changed":
            db.device_usb_intent_set(
                identity_id="alice", device_id="reader-a",
                artifact_id=ARTIFACT, desired_state="remove",
            )
        else:
            db.device_placement_put({
                "device_id": "reader-a", "artifact_id": ARTIFACT,
                "desired_state": "keep", "observed_state": "unknown",
                "target_relative_path": "Books/Cifereca/moved.epub",
                "metadata": {"intent_identity_id": "alice"},
            })
        return fake_scan()
    monkeypatch.setattr(run, "scan_managed_usb", altered)
    result = inspect(db, root, mount)
    assert result.status == "scope_changed"
    assert result.rows == ()
    assert result.executable is False


def test_fail_closed_on_scan_exception_without_device_write(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    def unavailable(**kwargs):
        raise OSError("bad USB / endpoint")
    monkeypatch.setattr(run, "scan_managed_usb", unavailable)
    result = inspect(db, root, mount)
    assert result.status == "scope_unverified"
    assert result.rows == ()


def test_no_intentions_stops_before_scanning(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_placement_delete("reader-a", ARTIFACT)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_:
                        pytest.fail("no intentions means no scan"))
    result = inspect(db, root, mount)
    assert result.status == "no_intentions"
    assert result.rows == ()


def test_no_explicit_hardware_validation_is_claimed(tmp_path):
    db, root, book, mount = setup(tmp_path)
    # A temp directory is not an independently attested Linux USB mount.
    # The real scanner rejects it; no actual hardware is used in this test.
    mount.mkdir()
    result = inspect(db, root, mount)
    assert result.status in ("device_unverified", "scope_unverified")
    assert result.scan_attested is False
    assert all(row.observed == "unknown" for row in result.rows)
