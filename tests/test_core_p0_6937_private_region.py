"""#6937: isolated registration, no adoption/retirement of existing bytes."""
import os
from pathlib import Path
import stat

import pytest

from cifereca.core import derived_variant_private_region as region
from cifereca.core import derived_variant_supervised_recovery as recovery
from cifereca.core.derived_variant_journal import DerivedJournalConflict
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from tests.test_core_p0_4520_derived_journal import _prepared_publication


@pytest.fixture
def setup(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    db = service.storage
    try:
        with InterProcessLock(lock_path) as lock:
            options = dict(library_root=tmp_path, identity_id="id1",
                           placement_id=intent.parent_placement_id, shared_lock=lock,
                           scope_current=lambda sql: True, explicitly_requested=True)
            yield db, intent, options
    finally:
        db.close()


def _region(intent):
    return Path(intent.parent_path).parent / region._REGION


def test_new_binding_durable_and_reopen_never_adopts_legacy(setup):
    db, intent, options = setup
    key = region._key("id1")
    directory = _region(intent)
    with pytest.raises(DerivedJournalConflict, match="not_registered"):
        region.ensure_private_derived_region(db, **options)
    assert not directory.exists()
    report = region.ensure_private_derived_region(db, **options, provision=True)
    assert report["status"] == "registered" and report["same_filesystem_verified"]
    assert not any(report[k] for k in ("consumer_exclusivity_verified",
                                       "retention_authorized", "destructive_cleanup_allowed"))
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / region._MARKER).stat().st_mode) == 0o600
    raw = db.metadata_get(key)
    other = Storage(db.path)
    try:
        again = region.ensure_private_derived_region(other, **options)
        assert again["status"] == "verified"
        assert other.metadata_get(key) == raw
        assert str(directory) not in repr(again)
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        other.close()


@pytest.mark.parametrize("change", ["name", "symlink", "missing_parent", "foreign_owner", "unconfirmed"])
def test_refused_initial_binding_never_adopts_or_mutates(setup, tmp_path, change):
    db, intent, options = setup
    directory = _region(intent)
    other = tmp_path / "other"
    if change == "name":
        directory.mkdir(mode=0o700)
        (directory / "old").write_bytes(b"unknown")
    elif change == "symlink":
        other.mkdir()
        directory.symlink_to(other, target_is_directory=True)
    elif change == "missing_parent":
        Path(intent.parent_path).unlink()
    elif change == "foreign_owner":
        options["identity_id"] = "foreign"
    else:
        options["explicitly_requested"] = False
    with pytest.raises(DerivedJournalConflict):
        region.ensure_private_derived_region(db, **options, provision=True)
    assert db.metadata_get(region._key("id1")) is None
    if change == "name":
        assert (directory / "old").read_bytes() == b"unknown"
        assert not (directory / region._MARKER).exists()
    elif change == "symlink":
        assert directory.is_symlink() and list(other.iterdir()) == []
    else:
        assert not directory.exists()
    assert Path(intent.staged_path).samefile(intent.candidate_path)


@pytest.mark.parametrize("change", ["marker", "mode", "rebind", "hardlink", "parent"])
def test_existing_binding_drift_fails_closed(setup, change):
    db, intent, options = setup
    directory = _region(intent)
    region.ensure_private_derived_region(db, **options, provision=True)
    raw = db.metadata_get(region._key("id1"))
    marker = directory / region._MARKER
    if change == "marker":
        marker.write_bytes(b"x" * 32)
    elif change == "mode":
        directory.chmod(0o755)
    elif change == "rebind":
        directory.rename(directory.with_name("moved-region"))
        directory.mkdir(mode=0o700)
        marker.write_bytes(b"0" * 32)
        marker.chmod(0o600)
    elif change == "hardlink":
        os.link(marker, directory / "alias")
    else:
        Path(intent.parent_path).unlink()
    with pytest.raises(DerivedJournalConflict):
        region.ensure_private_derived_region(db, **options, provision=True)
    assert db.metadata_get(region._key("id1")) == raw
    assert Path(intent.staged_path).samefile(intent.candidate_path)


def test_sql_failure_preserves_unregistered_region_and_never_adopts(setup):
    db, intent, options = setup
    key = region._key("id1")
    with db._lock, db._connect() as sql:
        sql.execute("CREATE TRIGGER synthetic_region_abort BEFORE INSERT ON metadata "
                    "WHEN NEW.key LIKE 'derived.publication.region.v1:%' "
                    "BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    with pytest.raises(DerivedJournalConflict):
        region.ensure_private_derived_region(db, **options, provision=True)
    directory = _region(intent)
    marker = directory / region._MARKER
    retained = marker.read_bytes()
    assert db.metadata_get(key) is None
    with db._lock, db._connect() as sql:
        sql.execute("DROP TRIGGER synthetic_region_abort")
    with pytest.raises(DerivedJournalConflict, match="unregistered_name"):
        region.ensure_private_derived_region(db, **options, provision=True)
    assert marker.read_bytes() == retained and db.metadata_get(key) is None
    assert Path(intent.staged_path).samefile(intent.candidate_path)


def test_device_mismatch_refuses_before_provision(setup, monkeypatch):
    db, intent, options = setup
    original = region._private_directory

    def different_device(fd, *, exact=False):
        dev, ino = original(fd, exact=exact)
        return (dev + 1, ino) if not exact else (dev, ino)

    monkeypatch.setattr(region, "_private_directory", different_device)
    with pytest.raises(DerivedJournalConflict, match="filesystem_mismatch"):
        region.ensure_private_derived_region(db, **options, provision=True)
    assert not _region(intent).exists()
    assert db.metadata_get(region._key("id1")) is None


def test_nested_transaction_not_touched(setup):
    db, intent, options = setup
    sql = db._connect()
    sql.execute("BEGIN IMMEDIATE")
    try:
        sql.execute("INSERT INTO metadata(key,value) VALUES('test.region.caller','pending')")
        with pytest.raises(DerivedJournalConflict, match="nested_transaction"):
            region.ensure_private_derived_region(db, **options, provision=True)
        assert sql.in_transaction
        assert sql.execute("SELECT value FROM metadata WHERE key='test.region.caller'").fetchone() == ("pending",)
        assert not _region(intent).exists()
    finally:
        sql.rollback()


@pytest.mark.parametrize("phase", ["parent", "parent_reopen", "region", "marker"])
def test_same_device_distinct_mount_refuses_without_adoption(setup, monkeypatch, phase):
    db, intent, options = setup
    directory = _region(intent)
    key = region._key("id1")
    if phase in ("region", "marker"):
        # Exercise a previously durable binding rather than leaving a
        # newly allocated but unregistered directory on this synthetic fault.
        region.ensure_private_derived_region(db, **options, provision=True)
    before = db.metadata_get(key)
    original = region._mount_id
    parent = Path(intent.parent_path)
    target = {"parent": parent, "parent_reopen": parent,
              "region": directory, "marker": directory / region._MARKER}[phase]
    seen = []

    def distinct_mount(fd):
        value = original(fd)
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if path == target:
            seen.append(path)
            if phase != "parent_reopen" or len(seen) >= 2:
                return value + 1000000
        return value

    monkeypatch.setattr(region, "_mount_id", distinct_mount)
    with pytest.raises(DerivedJournalConflict, match="filesystem_mismatch|scope_changed"):
        region.ensure_private_derived_region(db, **options, provision=True)
    assert seen and db.metadata_get(key) == before
    assert directory.exists() == (before is not None)
    assert Path(intent.staged_path).samefile(intent.candidate_path)


def test_missing_mount_information_refuses_before_creation(setup, monkeypatch):
    db, intent, options = setup
    def missing(fd):
        raise DerivedJournalConflict("derived_region_mount_unverified")
    monkeypatch.setattr(region, "_mount_id", missing)
    with pytest.raises(DerivedJournalConflict, match="mount_unverified"):
        region.ensure_private_derived_region(db, **options, provision=True)
    assert not _region(intent).exists()
    assert db.metadata_get(region._key("id1")) is None


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("change", ["same_size", "size", "sql_digest", "sql_size"])
def test_parent_artifact_must_match_pinned_bytes_and_sql(setup, registered, change):
    db, intent, options = setup
    directory = _region(intent)
    key = region._key("id1")
    if registered:
        region.ensure_private_derived_region(db, **options, provision=True)
    prior = db.metadata_get(key)
    parent = Path(intent.parent_path)
    before = parent.stat()
    original = parent.read_bytes()
    if change == "same_size":
        parent.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        os.utime(parent, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "size":
        parent.write_bytes(original + b"x")
    elif change == "sql_digest":
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE artifacts SET sha256=? WHERE artifact_id=?",
                        ("0" * 64, "sha256:" + intent.parent_sha256))
    else:
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE artifacts SET size=size+1 WHERE artifact_id=?",
                        ("sha256:" + intent.parent_sha256,))
    retained = parent.read_bytes()
    with pytest.raises(DerivedJournalConflict, match="parent_unverified"):
        region.ensure_private_derived_region(db, **options, provision=True)
    assert db.metadata_get(key) == prior
    assert directory.exists() is registered
    assert parent.read_bytes() == retained
    assert Path(intent.staged_path).samefile(intent.candidate_path)
