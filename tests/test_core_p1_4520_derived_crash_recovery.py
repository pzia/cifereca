"""#4520: explicit restart recovery of interrupted derived publication.

Filesystem and SQLite are synthetic and real. No network or supplier rights.
"""
from __future__ import annotations

import os
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


def test_normal_commit_preserves_receipt_as_non_destructive_evidence(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        journal = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        assert journal.is_file()
        observed = review_pending_derived_receipts(service, identity_id="id1")
        assert len(observed) == 1
        assert observed[0]["status"] == "already_committed"
        from cifereca.core.derived_variant_recovery import _pending_intent_key
        assert service.storage.metadata_get(_pending_intent_key(journal))
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
        # An inactive identity can be inspected but cannot commit a receipt.
        assert review_pending_derived_receipts(service, identity_id="id2") == ()
        with pytest.raises(ValueError, match="derived_recovery_active_identity_mismatch"):
            review_pending_derived_receipts(service, identity_id="id2", commit=True)
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
            import hashlib
            from cifereca.core.derived_variant_recovery import _pending_intent_key
            digest = hashlib.sha256(journals[-1].read_bytes()).hexdigest()
            assert service.storage.metadata_get(
                _pending_intent_key(journals[-1])
            ) == digest, "SQL intent must exist before os.link"
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


def test_forged_receipt_without_sql_intent_never_recovers(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        stage = tmp_path / "library" / ".cifereca-staging" / "variants"
        receipt = next(stage.glob("pending-*.json"))
        # Even byte-for-byte identical JSON has no authority under a newly
        # fabricated receipt filename absent from SQLite's prelink intents.
        forged = stage / ("pending-" + "a" * 32 + ".json")
        forged.write_bytes(receipt.read_bytes())
        observed = review_pending_derived_receipts(service, identity_id="id1")
        assert len(observed) == 2
        assert [x["status"] for x in observed].count("manual_review") == 1
        assert [x["status"] for x in observed].count("recoverable") == 1
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_mutated_receipt_rejected_even_with_approved_new_sha(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        stage = tmp_path / "library" / ".cifereca-staging" / "variants"
        receipt = next(stage.glob("pending-*.json"))
        import json
        import hashlib
        row = json.loads(receipt.read_text())
        row["book_id"] = "forged-edition"
        receipt.write_text(json.dumps(row), encoding="utf-8")
        altered = hashlib.sha256(receipt.read_bytes()).hexdigest()
        observed = review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
            approved_receipt_sha256=altered,
        )
        assert observed[0]["status"] == "manual_review"
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_recovery_commit_rejects_nonactive_identity(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        inspected = review_pending_derived_receipts(service, identity_id="id1")
        assert inspected[0]["status"] == "recoverable"
        service.current_identity.identity_id = "different"
        with pytest.raises(ValueError, match="derived_recovery_active_identity_mismatch"):
            review_pending_derived_receipts(
                service, identity_id="id1", commit=True,
                approved_receipt_sha256=inspected[0]["sha256"],
            )
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_removed_sql_intent_between_preview_and_transaction_refuses_commit(
    tmp_path, monkeypatch,
):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        from cifereca.core import derived_variant_recovery as recovery
        receipt = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        reviewed = recovery.review_pending_derived_receipts(service, identity_id="id1")
        assert reviewed[0]["status"] == "recoverable"
        original = recovery.publish_derived_variant

        def revoke_intent_just_before_commit(storage, **kwargs):
            # Simulate another worker revoking approval after initial review
            # but before the BEGIN IMMEDIATE of canonical publication.
            with storage._lock, storage._connect() as db:
                db.execute(
                    "DELETE FROM metadata WHERE key=?",
                    (recovery._pending_intent_key(receipt),),
                )
            return original(storage, **kwargs)

        monkeypatch.setattr(recovery, "publish_derived_variant",
                            revoke_intent_just_before_commit)
        outcome = recovery.review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
            approved_receipt_sha256=reviewed[0]["sha256"],
        )
        assert outcome[0]["status"] == "manual_review"
        assert service.storage.artifact_variants() == []
        assert receipt.exists()
    finally:
        service.storage.close()


@pytest.mark.parametrize("victim", ["source", "target"])
def test_recovery_refuses_inode_swap_between_review_and_sql(
    tmp_path, monkeypatch, victim,
):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        from cifereca.core import derived_variant_recovery as recovery
        import json
        journal = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        receipt = json.loads(journal.read_text())
        path = Path(receipt["source_path" if victim == "source"
                           else "published_path"])
        earlier = path.read_bytes()
        reviewed = recovery.review_pending_derived_receipts(
            service, identity_id="id1",
        )
        assert reviewed[0]["status"] == "recoverable"
        original = recovery.publish_derived_variant

        def replace_inode_after_review(storage, **kwargs):
            # Simulate an adversarial unlink+recreate after the second
            # pre-commit SHA verification but before BEGIN IMMEDIATE.
            alternate = path.with_name(path.name + ".adversary")
            alternate.write_bytes(earlier)
            os.replace(alternate, path)
            return original(storage, **kwargs)

        monkeypatch.setattr(
            recovery, "publish_derived_variant", replace_inode_after_review,
        )
        outcome = recovery.review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
            approved_receipt_sha256=reviewed[0]["sha256"],
        )
        assert outcome[0]["status"] == "manual_review"
        assert service.storage.artifact_variants() == []
        assert service.storage.placements(state="present") == [
            service.storage.placement_get(
                next(p["placement_id"] for p in service.storage.placements()
                     if p["artifact_id"] == source.artifact_id)
            )
        ]
        assert journal.exists()
    finally:
        service.storage.close()


def test_receipt_replaced_before_sql_intent_is_not_registered(tmp_path, monkeypatch):
    service, source, _ = _library(tmp_path)
    try:
        from cifereca.core import derived_variant_recovery as recovery
        original = recovery._read_receipt_nofollow
        replaced = []

        def tamper_before_register(receipt):
            if not replaced:
                original_bytes = original(receipt)
                receipt.write_bytes(original_bytes + b" ")
                replaced.append(receipt)
            return original(receipt)

        monkeypatch.setattr(recovery, "_read_receipt_nofollow",
                            tamper_before_register)
        with pytest.raises(ValueError, match="derived_receipt_changed_before_intent"):
            ArtifactExecutionBrokerImpl(service).execute_transform(
                TransformRequest(source, "optimize"), control=Control(),
            )
        assert len(replaced) == 1
        from cifereca.core.derived_variant_recovery import _pending_intent_key
        assert service.storage.metadata_get(_pending_intent_key(replaced[0])) is None
        assert service.storage.artifact_variants() == []
        assert [p for p in service.storage.placements()
                if p["artifact_id"] != source.artifact_id] == []
    finally:
        service.storage.close()


def test_successful_publish_preserves_foreign_receipt_replacement(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        result = ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        journal = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        assert journal.is_file()
        alternate = journal.with_name(journal.name + ".foreign")
        alternate.write_bytes(b"concurrent foreign receipt")
        os.replace(alternate, journal)
        assert journal.read_bytes() == b"concurrent foreign receipt"
        assert review_pending_derived_receipts(
            service, identity_id="id1",
        )[0]["status"] == "manual_review"
        assert len(service.storage.artifact_variants(artifact_id=result.artifact_id)) == 1
    finally:
        service.storage.close()


def test_recreated_source_with_identical_bytes_does_not_establish_causality(
    tmp_path,
):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        root = tmp_path / "library"
        original_path = root / "Book.epub"
        saved_bytes = original_path.read_bytes()
        alternate = original_path.with_name("Book-replacement.epub")
        alternate.write_bytes(saved_bytes)
        os.replace(alternate, original_path)
        observed = review_pending_derived_receipts(service, identity_id="id1")
        assert observed[0]["status"] == "manual_review"
        assert service.storage.artifact_variants() == []
        assert original_path.read_bytes() == saved_bytes
    finally:
        service.storage.close()


def test_successful_publish_preserves_same_bytes_replaced_receipt(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        result = ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        journal = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        original_bytes = journal.read_bytes()
        original_inode = journal.lstat().st_ino
        alternate = journal.with_name(journal.name + ".replacement")
        alternate.write_bytes(original_bytes)
        os.replace(alternate, journal)
        assert journal.is_file()
        assert journal.read_bytes() == original_bytes
        assert journal.lstat().st_ino != original_inode
        reviewed = review_pending_derived_receipts(
            service, identity_id="id1",
        )
        assert reviewed[0]["status"] == "already_committed"
        assert len(service.storage.artifact_variants(artifact_id=result.artifact_id)) == 1
    finally:
        service.storage.close()


def test_successful_publication_never_unlinks_any_receipt(tmp_path, monkeypatch):
    service, source, _ = _library(tmp_path)
    try:
        original_unlink = os.unlink
        deleted_names = []

        def guard_unlink(name, *args, **kwargs):
            deleted_names.append(str(name))
            assert not Path(str(name)).name.startswith("pending-"), (
                "publication must never delete a receipt, even after SQL commit"
            )
            return original_unlink(name, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", guard_unlink)
        child = ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        assert child.artifact_id != source.artifact_id
        assert any(name.endswith(".epub") for name in deleted_names)
        observed = review_pending_derived_receipts(service, identity_id="id1")
        assert [r["status"] for r in observed] == ["already_committed"]
    finally:
        service.storage.close()


def test_retained_receipts_paginate_without_deletion_or_unbounded_result(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        from cifereca.core.derived_variant_recovery import MAX_RECEIPTS
        _failed_publication(service, source)
        stage = tmp_path / "library" / ".cifereca-staging" / "variants"
        for number in range(MAX_RECEIPTS):
            foreign = stage / ("pending-" + f"{number:032x}" + ".json")
            foreign.write_bytes(b"{}")
        first = review_pending_derived_receipts(service, identity_id="id1")
        assert len(first) == MAX_RECEIPTS
        assert first[-1].get("next_cursor") == first[-1]["cursor"]
        second = review_pending_derived_receipts(
            service, identity_id="id1", after_cursor=first[-1]["next_cursor"],
        )
        assert len(second) == 1
        assert "next_cursor" not in second[-1]
        cursors = {item["cursor"] for item in first + second}
        assert len(cursors) == MAX_RECEIPTS + 1
        assert all("receipt" in item and "status" in item
                   for item in first + second)
        assert service.storage.artifact_variants() == []
        assert len(list(stage.glob("pending-*.json"))) == MAX_RECEIPTS + 1
    finally:
        service.storage.close()


def test_recovery_rejects_invalid_pagination_cursor(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        for invalid in (None, 42, "a" * 513, "\\x00"):
            with pytest.raises(ValueError, match="derived_recovery_cursor_invalid"):
                review_pending_derived_receipts(
                    service, identity_id="id1", after_cursor=invalid,
                )
        assert len(review_pending_derived_receipts(service, identity_id="id1")) == 1
    finally:
        service.storage.close()
