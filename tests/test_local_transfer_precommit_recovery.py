"""Explicit local sends recover their own bytes, never a same-SHA B file."""
from pathlib import Path
import os
import json
import subprocess
import sys

import pytest

from cifereca.core import canonical_transfer
from tests.test_core_p1_3275_live_canonical_transfer import prepare, canon_rows, BYTES
from tests.test_core0925_open_destinations import _service


@pytest.fixture
def case(tmp_path, monkeypatch):
    service, root, a, placement, artifact = prepare(tmp_path)
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    monkeypatch.setattr(canonical_transfer, "acquisition_lock_file", lambda: tmp_path / "shared.lock")
    try:
        yield service, root, a, placement
    finally:
        service.close()


def test_sql_rollback_retry_reuses_same_precommit_copy(case, monkeypatch):
    service, root, a, placement = case
    with service.storage._lock, service.storage._connect() as db:
        db.execute("CREATE TRIGGER fail_transfer BEFORE INSERT ON origin_links "
                   "WHEN NEW.origin_kind='cifereca' BEGIN SELECT RAISE(ABORT,'test_failure'); END")
    with pytest.raises(Exception, match="test_failure"):
        service.materialize_to_identity(placement, "bob")
    b = root / "bob/local/book.pdf"
    before = b.stat().st_ino
    assert canon_rows(service.storage)["placements"] == 1
    assert canon_rows(service.storage)["origin_links"] == 0
    with service.storage._lock, service.storage._connect() as db:
        db.execute("DROP TRIGGER fail_transfer")
    result = service.materialize_to_identity(placement, "bob")
    assert Path(result["path"]) == b and b.stat().st_ino == before
    assert b.read_bytes() == a.read_bytes() == BYTES
    assert list(b.parent.iterdir()) == [b]
    assert canon_rows(service.storage)["origin_links"] == 2
    assert service.storage.entitlements() == []


def _crash(root, phase):
    script = r'''
import os, sys
from pathlib import Path
from tests.test_core_p1_3275_live_canonical_transfer import prepare
import cifereca.paths as paths
import cifereca.core.canonical_transfer as publication
root, phase = Path(sys.argv[1]), sys.argv[2]
paths.acquisition_lock_file = lambda: root / "shared.lock"
publication.acquisition_lock_file = paths.acquisition_lock_file
service, library, a, placement, artifact = prepare(root)
if phase == "before_link":
    link = os.link
    def crash_link(src, dst, *args, **kwargs):
        if Path(src).name.startswith(".cifereca-send-"):
            os._exit(37)
        return link(src, dst, *args, **kwargs)
    os.link = crash_link
elif phase == "during_commit":
    from contextlib import contextmanager
    connect = service.storage._connect
    @contextmanager
    def crash_connection():
        with connect() as db:
            db.create_function("test_transfer_crash", 0, lambda: os._exit(37))
            yield db
    service.storage._connect = crash_connection
    with service.storage._lock, service.storage._connect() as db:
        db.execute("CREATE TRIGGER crash_transfer BEFORE INSERT ON origin_links "
                   "WHEN NEW.origin_kind='cifereca' BEGIN SELECT test_transfer_crash(); END")
else:
    publication.publish_completed_local_transfer = lambda *a, **k: os._exit(37)
service.materialize_to_identity(placement, "bob")
raise AssertionError("crash boundary missed")
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), phase],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 37, result.stderr


@pytest.mark.parametrize("phase", ["before_link", "before_commit", "during_commit"])
def test_real_process_crash_recovers_completed_copy_without_duplicate(
    tmp_path, monkeypatch, phase,
):
    _crash(tmp_path, phase)
    service = _service(tmp_path)
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    monkeypatch.setattr("cifereca.core.canonical_transfer.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    root = tmp_path / "EBooks"
    a, b = root / "alice/local/book.pdf", root / "bob/local/book.pdf"
    try:
        copies = list(b.parent.iterdir())
        original = next(p for p in copies if p.name.startswith(".cifereca-send-")) if phase == "before_link" else b
        inode = original.stat().st_ino
        assert canon_rows(service.storage)["placements"] == 1
        assert canon_rows(service.storage)["origin_links"] == 0
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DROP TRIGGER IF EXISTS crash_transfer")
        source = service.storage.placement_for_path(a)
        result = service.materialize_to_identity(source["placement_id"], "bob")
        assert Path(result["path"]) == b and b.stat().st_ino == inode
        assert list(b.parent.iterdir()) == [b]
        assert canon_rows(service.storage)["placements"] == 2
        assert canon_rows(service.storage)["origin_links"] == 2
    finally:
        service.close()


def test_same_sha_foreign_destination_after_crash_is_not_adopted(tmp_path, monkeypatch):
    _crash(tmp_path, "before_link")
    b = tmp_path / "EBooks/bob/local/book.pdf"
    b.write_bytes(BYTES)
    inode = b.stat().st_ino
    service = _service(tmp_path)
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    monkeypatch.setattr("cifereca.core.canonical_transfer.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    try:
        source = service.storage.placement_for_path(tmp_path / "EBooks/alice/local/book.pdf")
        with pytest.raises(ValueError, match="local_transfer_copy_changed"):
            service.materialize_to_identity(source["placement_id"], "bob")
        assert b.stat().st_ino == inode and b.read_bytes() == BYTES
        assert canon_rows(service.storage)["origin_links"] == 0
        assert canon_rows(service.storage)["placements"] == 1
    finally:
        service.close()


def test_checkpoint_is_only_operation_history_and_never_a_provider_receipt(case):
    service, root, a, placement = case
    result = service.materialize_to_identity(placement, "bob")
    journal, = service.storage.acquisitions()
    assert (journal["provider_id"], journal["credential_id"], journal["identity_id"]) == (
        "cifereca", "local", "bob",
    )
    assert journal["status"] == "local_transfer_published"
    assert journal["entitlement_id"] is None and journal["artifact_id"] is None
    assert journal["strategy"] == "local_transfer"
    assert service.storage.entitlements() == []
    b = Path(result["path"])
    b.unlink()
    # Only another explicit send may create this copy again.
    replay = service.materialize_to_identity(placement, "bob")
    assert Path(replay["path"]).read_bytes() == BYTES
    assert canon_rows(service.storage)["origin_links"] == 2
    b.unlink()
    service.catalog_scan()
    assert not b.exists()  # Neither scan nor journal is an automatic restore.


def test_completed_recipient_file_modified_by_b_is_preserved_on_new_send(case):
    service, root, a, placement = case
    first = service.materialize_to_identity(placement, "bob")
    b = Path(first["path"])
    b.write_bytes(b"B's independent edits")
    inode = b.stat().st_ino
    second = service.materialize_to_identity(placement, "bob")
    assert b.read_bytes() == b"B's independent edits" and b.stat().st_ino == inode
    assert Path(second["path"]) != b
    assert Path(second["path"]).read_bytes() == a.read_bytes() == BYTES


@pytest.mark.parametrize("change", ["inode", "bytes", "symlink"])
def test_precommit_copy_replaced_or_changed_is_not_published(tmp_path, monkeypatch, change):
    _crash(tmp_path, "before_commit")
    b = tmp_path / "EBooks/bob/local/book.pdf"
    if change == "inode":
        replacement = b.with_name("replacement")
        replacement.write_bytes(BYTES)
        replacement.replace(b)
    elif change == "bytes":
        b.write_bytes(b"%PDF-1.7\nChanged B bytes\n")
    else:
        b.unlink()
        b.symlink_to(tmp_path / "EBooks/alice/local/book.pdf")
    service = _service(tmp_path)
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    monkeypatch.setattr("cifereca.core.canonical_transfer.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    try:
        source = service.storage.placement_for_path(tmp_path / "EBooks/alice/local/book.pdf")
        with pytest.raises(ValueError, match="local_transfer_copy_changed"):
            service.materialize_to_identity(source["placement_id"], "bob")
        assert canon_rows(service.storage)["origin_links"] == 0
        assert canon_rows(service.storage)["placements"] == 1
    finally:
        service.close()


@pytest.mark.parametrize("change", ["scope", "path", "pin"])
def test_contradictory_checkpoint_cannot_publish_foreign_bytes(tmp_path, monkeypatch, change):
    _crash(tmp_path, "before_commit")
    service = _service(tmp_path)
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    monkeypatch.setattr("cifereca.core.canonical_transfer.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    try:
        record, = service.storage.acquisitions()
        metadata = record["metadata"]
        if change == "scope":
            metadata["scope"]["to_identity_id"] = "another-recipient"
        elif change == "path":
            metadata["destination"] = str(tmp_path / "outside.pdf")
        else:
            metadata["pin"] = [True, 1, 1, 1]
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                       (json.dumps(metadata), record["acquisition_id"]))
        source = service.storage.placement_for_path(tmp_path / "EBooks/alice/local/book.pdf")
        with pytest.raises(ValueError, match="local_transfer_journal_conflict"):
            service.materialize_to_identity(source["placement_id"], "bob")
        assert canon_rows(service.storage)["origin_links"] == 0
        assert canon_rows(service.storage)["placements"] == 1
    finally:
        service.close()


def test_lost_source_artifact_cannot_downgrade_checkpoint_to_legacy_send(tmp_path, monkeypatch):
    _crash(tmp_path, "before_commit")
    service = _service(tmp_path)
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    monkeypatch.setattr("cifereca.core.canonical_transfer.acquisition_lock_file", lambda: tmp_path / "shared.lock")
    try:
        source = service.storage.placement_for_path(tmp_path / "EBooks/alice/local/book.pdf")
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM artifacts WHERE artifact_id=?", (source["artifact_id"],))
        with pytest.raises(ValueError, match="local_transfer_source_changed"):
            service.materialize_to_identity(source["placement_id"], "bob")
        assert len(list((tmp_path / "EBooks/bob/local").glob("*.pdf"))) == 1
        assert canon_rows(service.storage)["origin_links"] == 0
        assert canon_rows(service.storage)["placements"] == 1
    finally:
        service.close()
