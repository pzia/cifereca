"""#4520: explicit restart recovery of interrupted derived publication.

Filesystem and SQLite are synthetic and real. No network or supplier rights.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cifereca.storage import Storage
from cifereca.core.derived_variant_recovery import review_pending_derived_receipts
from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
from cifereca.plugin_api import TransformRequest
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


def _library(tmp_path):
    service, source, source_pid = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    return service, source, source_pid


def _failed_publication(service, source):
    # The hardlink and fsynced prelink receipt are durable before SQL starts.
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "CREATE TRIGGER fail_recovery_insert BEFORE INSERT ON origin_links "
            "WHEN NEW.origin_kind='derived' BEGIN "
            "SELECT RAISE(ABORT,'simulated_crash_before_sql'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="simulated_crash_before_sql"):
        ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
    with service.storage._lock, service.storage._connect() as db:
        db.execute("DROP TRIGGER fail_recovery_insert")


def test_receipts_are_removed_after_normal_atomic_commit(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        assert review_pending_derived_receipts(service, identity_id="id1") == ()
    finally:
        service.storage.close()


def test_fsync_receipt_can_explicitly_restore_after_sql_interruption(tmp_path):
    service, source, source_pid = _library(tmp_path)
    try:
        _failed_publication(service, source)
        assert service.storage.artifact_variants() == []
        assert [p for p in service.storage.placements()
                if p["artifact_id"] != source.artifact_id] == []
        journal = list((tmp_path / "library" / ".cifereca-staging" / "variants").glob("pending-*.json"))
        assert len(journal) == 1
        # Exercise an actual SQLite service restart, not merely a second call
        # against the same in-memory connection and plugin manager.
        service.storage.close()
        service.storage = Storage(tmp_path / "db.sqlite3")
        before = review_pending_derived_receipts(service, identity_id="id1")
        assert len(before) == 1 and before[0]["status"] == "recoverable"
        assert service.storage.artifact_variants() == []
        # No unattended bulk promotion from a user-writable JSON journal.
        denied = review_pending_derived_receipts(service, identity_id="id1", commit=True)
        assert denied[0]["status"] == "approval_required"
        done = review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
            approved_receipt_sha256=before[0]["sha256"],
        )
        assert done[0]["status"] == "recovered"
        child = service.storage.artifact_variants()[0]["artifact_id"]
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM origin_links "
                "WHERE identity_id='id1' AND artifact_id=? "
                "AND origin_kind='derived'", (child,),
            ).fetchone()[0] == 1
        assert service.storage.placement_get(source_pid)["artifact_id"] == source.artifact_id
        assert len([p for p in service.storage.placements() if p["artifact_id"] == child]) == 1
        # Repeated restart never creates a second OriginLink nor an entitlement.
        again = review_pending_derived_receipts(service, identity_id="id1", commit=True)
        assert again[0]["status"] == "already_committed"
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM entitlements").fetchone()[0] == 0
    finally:
        service.storage.close()


def test_other_profile_cannot_replay_first_profiles_receipt(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        service.storage.identity_put({
            "identity_id": "id2", "label": "Second", "directory_name": "another",
        })
        (tmp_path / "another").mkdir()
        assert review_pending_derived_receipts(service, identity_id="id2", commit=True) == ()
        assert len(review_pending_derived_receipts(service, identity_id="id1")) == 1
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_modified_output_refuses_recovery_without_removing_file(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        output = tmp_path / "library" / "Optimized.epub"
        # Fake transformer may propose another fixed name; derive from the
        # single recovered receipt to avoid assuming file naming policy.
        import json
        journal = next((tmp_path / "library" / ".cifereca-staging" / "variants").glob("pending-*.json"))
        output = Path(json.loads(journal.read_text())["published_path"])
        output.write_bytes(b"tampered by another writer")
        assert review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
        )[0]["status"] == "manual_review"
        assert output.read_bytes() == b"tampered by another writer"
        assert service.storage.artifact_variants() == []
        assert journal.exists()
    finally:
        service.storage.close()


def test_symlink_substitution_never_promotes_or_deletes(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        import json
        journal = next((tmp_path / "library" / ".cifereca-staging" / "variants").glob("pending-*.json"))
        output = Path(json.loads(journal.read_text())["published_path"])
        elsewhere = tmp_path / "important.epub"
        elsewhere.write_bytes(output.read_bytes())
        output.unlink()
        output.symlink_to(elsewhere)
        assert review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
        )[0]["status"] == "manual_review"
        assert elsewhere.read_bytes() == b"book-data-optimized"
        assert output.is_symlink()
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_receipt_is_durable_before_any_exclusive_hardlink(tmp_path, monkeypatch):
    service, source, _ = _library(tmp_path)
    try:
        import os
        from cifereca.plugin_api import Conflict
        checked = []

        def link_refused(*args, **kwargs):
            journals = list((
                tmp_path / "library" / ".cifereca-staging" / "variants"
            ).glob("pending-*.json"))
            assert journals, "receipt must be visible before os.link"
            # A genuine prelink receipt is fsynced and has the exact origin.
            import json
            row = json.loads(journals[-1].read_text())
            assert row["source_artifact_id"] == source.artifact_id
            assert row["identity_id"] == "id1"
            checked.append(row["published_path"])
            raise FileExistsError("synthetic publish collision")

        monkeypatch.setattr(os, "link", link_refused)
        with pytest.raises(Conflict):
            ArtifactExecutionBrokerImpl(service).execute_transform(
                TransformRequest(source, "optimize"), control=Control(),
            )
        assert len(checked) == 2  # primary name and short-SHA alternative
        assert review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
        )
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_changed_source_refuses_recovery_without_tampering_with_orphan(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        import json
        journal = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        output = Path(json.loads(journal.read_text())["published_path"])
        prior = output.read_bytes()
        (tmp_path / "library" / "Book.epub").write_bytes(b"changed-parent")
        status = review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
        )
        assert status[0]["status"] == "manual_review"
        assert output.read_bytes() == prior
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_receipt_symlink_is_never_followed_or_promoted(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        stage = tmp_path / "library" / ".cifereca-staging" / "variants"
        receipt = next(stage.glob("pending-*.json"))
        outside = tmp_path / "original-receipt.json"
        outside.write_bytes(receipt.read_bytes())
        receipt.unlink()
        receipt.symlink_to(outside)
        observed = review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
        )
        assert observed[0]["status"] == "manual_review"
        assert outside.exists()
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_receipt_forged_output_size_is_not_recoverable(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        import json
        receipt = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        record = json.loads(receipt.read_text())
        record["output_size"] += 1
        receipt.write_text(json.dumps(record), encoding="utf-8")
        observed = review_pending_derived_receipts(service, identity_id="id1")
        assert observed[0]["status"] == "manual_review"
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()
