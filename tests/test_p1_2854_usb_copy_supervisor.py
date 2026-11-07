"""Durable supervisor transitions with an isolated SQLite contract adapter."""
from contextlib import ExitStack, closing
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import pickle
import sqlite3
import time
from types import SimpleNamespace

import pytest

from cifereca.core import device_usb_copy_recovery as recovery
from cifereca.core import device_usb_copy_supervisor as supervisor
from cifereca.core.device_usb_copy_admission import UsbCopyConsent, UsbCopyRightsEvidence
from cifereca.core.device_usb_mount import LinuxUsbMount
from cifereca.locking import InterProcessLock
from cifereca.plugin_api import USB_STAGE_CAPABILITY


class SqliteTestJournal:
    """Test-only backend. Production schema and observation writes are separate."""

    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, volume TEXT, target TEXT, phase TEXT, value BLOB
                );
                CREATE UNIQUE INDEX IF NOT EXISTS pending_destination
                ON operations(volume, target) WHERE phase != 'completed';
            """)

    def begin(self, entry):
        consent = entry.consent
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "INSERT OR IGNORE INTO operations VALUES (?, ?, ?, ?, ?)",
                (consent.operation_id, consent.usb_fingerprint,
                 consent.target_relative_path.casefold(), entry.phase, pickle.dumps(entry)),
            )
            return row.rowcount == 1

    def get(self, operation_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT value FROM operations WHERE id=?", (operation_id,),
            ).fetchone()
        return pickle.loads(row[0]) if row is not None else None

    def compare_and_set(self, before, after):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM operations WHERE id=?", (before.consent.operation_id,),
            ).fetchone()
            if row is None or pickle.loads(row[0]) != before:
                return False
            db.execute(
                "UPDATE operations SET phase=?, value=? WHERE id=?",
                (after.phase, pickle.dumps(after), before.consent.operation_id),
            )
        return True


@pytest.fixture
def copy(tmp_path, monkeypatch):
    volume = tmp_path / "volume"
    managed = volume / "Books"
    managed.mkdir(parents=True)
    device = managed.stat().st_dev
    mount = LinuxUsbMount(
        "alice", "reader-a", "uuid-reader-a", volume, "ext4",
        os.major(device), os.minor(device), 1800000000, 45,
    )
    data = b"native staged USB file"
    digest = sha256(data).hexdigest()
    now = int(time.time())
    consent = UsbCopyConsent(
        "alice", "reader-a", "uuid-reader-a", "sha256:" + digest, digest,
        "epub", "Books/" + digest + ".epub", "a" * 32, now - 1, now + 60,
    )
    rights = UsbCopyRightsEvidence(
        "alice", consent.artifact_id, digest, "epub", "source:fixture", "example",
        "exact-account", "purchase", "policy:fixture", "uuid-reader-a", "reader-a",
        now - 1, now + 60, personal_usb_copy_permitted=True,
    )
    journal = SqliteTestJournal(tmp_path / "journal.sqlite3")
    journal_at_stage = []

    def write(payload, append):
        entry = journal.get(consent.operation_id)
        assert entry is not None and entry.phase == "pending"
        journal_at_stage.append(entry)
        append(payload)

    service = SimpleNamespace(write_usb_stage=write)
    resolution = SimpleNamespace(
        service=service, plugin_id="storage.usb", registry_generation=8,
        capabilities=(USB_STAGE_CAPABILITY,),
    )
    monkeypatch.setattr(recovery, "_boot_id", lambda: "15d003bd-94a9-4317-a221-292d3b465fe0")
    monkeypatch.setattr(recovery.linux_mount, "_fd_mount_id", lambda _: 45)
    with ExitStack() as stack:
        directory_fd = os.open(managed, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, directory_fd)
        yield dict(
            journal=journal, lock_root=tmp_path / "locks", mount=mount,
            directory_fd=directory_fd, consent=consent, rights=rights, payload=data,
            plugin_manager=SimpleNamespace(services=lambda _: (resolution,)),
            plugin_id=resolution.plugin_id, registry_generation=resolution.registry_generation,
            revalidate=lambda c, r: c == consent and r == rights,
        ), managed, journal_at_stage, resolution


def test_copy_persists_pending_before_stage_and_receipt_after_publication(copy):
    args, managed, observations, _ = copy
    result = supervisor.execute_usb_copy(**args)
    consent = args["consent"]
    assert result.status == "completed"
    assert result.receipt is not None
    assert (managed / Path(consent.target_relative_path).name).read_bytes() == args["payload"]
    assert len(list(managed.iterdir())) == 1
    assert len(observations) == 1 and observations[0].pin is None
    reopened = SqliteTestJournal(args["journal"].path).get(consent.operation_id)
    assert reopened.phase == "completed"
    assert reopened.receipt == result.receipt
    assert reopened.pin is not None
    again = supervisor.execute_usb_copy(**args)
    assert again.reason == "usb_copy_consent_already_used"
    assert len(observations) == 1


def test_receipt_commit_failure_retains_checkpoint_for_supervised_recovery(copy, monkeypatch):
    args, managed, _, _ = copy
    journal = args["journal"]
    original = journal.compare_and_set

    def fail_completion(before, after):
        return False if after.phase == "completed" else original(before, after)

    monkeypatch.setattr(journal, "compare_and_set", fail_completion)
    result = supervisor.execute_usb_copy(**args)
    assert result.status == "recovery_required"
    assert result.reason == "usb_copy_receipt_uncommitted"
    assert result.receipt is None
    pending = SqliteTestJournal(journal.path).get(args["consent"].operation_id)
    assert pending.phase == "checkpointed" and pending.pin is not None
    assert pending.receipt is None
    assert (managed / Path(args["consent"].target_relative_path).name).exists()


def test_preexisting_target_is_not_adopted_even_with_exact_sha(copy):
    args, managed, observations, _ = copy
    target = managed / Path(args["consent"].target_relative_path).name
    target.write_bytes(args["payload"])
    assert supervisor.execute_usb_copy(**args).reason == "usb_copy_target_not_clear"
    assert args["journal"].get(args["consent"].operation_id) is None
    assert not observations
    assert list(managed.iterdir()) == [target]


def test_concurrent_reader_lock_stops_before_journal_and_copy(copy):
    args, managed, _, _ = copy
    key = sha256(args["consent"].usb_fingerprint.encode()).hexdigest() + ".lock"
    with InterProcessLock(args["lock_root"] / key):
        result = supervisor.execute_usb_copy(**args)
    assert result.reason == "usb_copy_reader_busy"
    assert args["journal"].get(args["consent"].operation_id) is None
    assert list(managed.iterdir()) == []


def test_revoked_authorization_before_publish_leaves_only_pending_stage(copy):
    args, managed, _, resolution = copy
    original = resolution.service.write_usb_stage

    def write(payload, append):
        original(payload, append)
        resolution.registry_generation += 1

    resolution.service.write_usb_stage = write
    result = supervisor.execute_usb_copy(**args)
    assert result.status == "recovery_required"
    assert result.receipt is None
    assert list(managed.iterdir()) == [managed / (".cifereca-usb-" + "a" * 32 + ".part")]
    pending = args["journal"].get(args["consent"].operation_id)
    assert pending.phase == "pending"


def test_expired_consent_never_creates_journal_or_file(copy):
    args, managed, _, _ = copy
    consent = replace(args["consent"], expires_at=int(time.time()) - 1)
    result = supervisor.execute_usb_copy(**(args | {"consent": consent}))
    assert result.reason == "usb_copy_authorization_unverified"
    assert args["journal"].get(consent.operation_id) is None
    assert list(managed.iterdir()) == []


def test_final_inode_replaced_before_receipt_is_not_adopted(copy):
    args, managed, _, _ = copy
    final = managed / Path(args["consent"].target_relative_path).name
    after_rename = 0

    def revalidate(_consent, _rights):
        nonlocal after_rename
        if final.exists():
            after_rename += 1
            if after_rename == 2:
                final.rename(final.with_suffix(".moved"))
                final.write_bytes(args["payload"])
        return True

    result = supervisor.execute_usb_copy(**(args | {"revalidate": revalidate}))
    assert result.status == "recovery_required"
    assert result.reason == "usb_copy_final_changed"
    assert args["journal"].get(args["consent"].operation_id).receipt is None
    assert final.read_bytes() == args["payload"]
