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
        for invalid in (None, 42, "a" * 513, chr(0)):
            with pytest.raises(ValueError, match="derived_recovery_cursor_invalid"):
                review_pending_derived_receipts(
                    service, identity_id="id1", after_cursor=invalid,
                )
        assert len(review_pending_derived_receipts(service, identity_id="id1")) == 1
    finally:
        service.storage.close()


def test_deeply_nested_profile_receipt_is_discovered_without_deletion(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        root = tmp_path / "library"
        nested = root / "Genre" / "Author" / "Series" / "Volume"
        nested.mkdir(parents=True)
        journal = nested / ".cifereca-staging" / "variants"
        journal.mkdir(parents=True)
        candidate = journal / ("pending-" + "f" * 32 + ".json")
        candidate.write_bytes(b"{}")
        results = review_pending_derived_receipts(service, identity_id="id1")
        assert len(results) == 1
        assert results[0]["status"] == "manual_review"
        assert results[0]["cursor"] == candidate.relative_to(root).as_posix()
        assert candidate.read_bytes() == b"{}"
    finally:
        service.storage.close()


def test_symlinked_nested_directory_is_never_traversed(tmp_path):
    service, source, _ = _library(tmp_path)
    try:
        root = tmp_path / "library"
        outside = tmp_path / "other-profile" / "Deep"
        (outside / ".cifereca-staging" / "variants").mkdir(parents=True)
        foreign = (
            outside / ".cifereca-staging" / "variants"
            / ("pending-" + "d" * 32 + ".json")
        )
        foreign.write_bytes(b"foreign")
        (root / "Shortcut").symlink_to(outside, target_is_directory=True)
        assert review_pending_derived_receipts(service, identity_id="id1") == ()
        assert foreign.read_bytes() == b"foreign"
    finally:
        service.storage.close()


def test_nested_receipt_walker_rejects_unbounded_depth_and_directory_count(
    tmp_path, monkeypatch,
):
    service, source, _ = _library(tmp_path)
    try:
        from cifereca.core import derived_variant_recovery as recovery
        deepest = tmp_path / "library"
        for index in range(5):
            deepest = deepest / f"level-{index}"
            deepest.mkdir()
        monkeypatch.setattr(recovery, "MAX_SCAN_DEPTH", 3)
        with pytest.raises(ValueError, match="derived_recovery_depth_limit"):
            recovery.review_pending_derived_receipts(service, identity_id="id1")
        monkeypatch.setattr(recovery, "MAX_SCAN_DEPTH", 32)
        monkeypatch.setattr(recovery, "MAX_SCAN_DIRECTORIES", 2)
        with pytest.raises(ValueError, match="derived_recovery_directory_limit"):
            recovery.review_pending_derived_receipts(service, identity_id="id1")
        assert deepest.is_dir()
    finally:
        service.storage.close()


def test_nested_publication_crash_is_recoverable_by_supervised_scanner(
    tmp_path,
):
    service, source, source_pid = _library(tmp_path)
    try:
        folder = tmp_path / "library" / "Collections" / "History" / "Volume"
        folder.mkdir(parents=True)
        original = tmp_path / "library" / "Book.epub"
        relocated = folder / original.name
        original.rename(relocated)
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE placements SET path=? WHERE placement_id=?",
                (str(relocated), source_pid),
            )
        _failed_publication(service, source)
        staged = folder / ".cifereca-staging" / "variants"
        assert len(list(staged.glob("pending-*.json"))) == 1
        review = review_pending_derived_receipts(
            service, identity_id="id1",
        )
        assert len(review) == 1
        assert review[0]["status"] == "recoverable"
        assert review[0]["cursor"].startswith("Collections/History/Volume/")
        restored = review_pending_derived_receipts(
            service, identity_id="id1", commit=True,
            approved_receipt_sha256=review[0]["sha256"],
        )
        assert restored[0]["status"] == "recovered"
        assert len(service.storage.artifact_variants()) == 1
        assert review_pending_derived_receipts(
            service, identity_id="id1",
        )[0]["status"] == "already_committed"
    finally:
        service.storage.close()


def test_nofollow_profile_open_rejects_intermediate_symlink(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        _open_under_root_nofollow, _read_receipt_nofollow,
    )
    root = tmp_path / "profile"
    outside = tmp_path / "foreign"
    root.mkdir()
    outside.mkdir()
    (outside / "pending-file.json").write_bytes(b'{"secret":"other-profile"}')
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    candidate = root / "redirect" / "pending-file.json"
    with pytest.raises(OSError):
        _open_under_root_nofollow(root, candidate)
    with pytest.raises(OSError):
        _read_receipt_nofollow(candidate, profile_root=root)
    assert candidate.read_bytes() == b'{"secret":"other-profile"}'


def test_nofollow_profile_open_rejects_parent_escape(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        _open_under_root_nofollow,
    )
    root = tmp_path / "profile"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"foreign")
    with pytest.raises(ValueError, match="relative"):
        _open_under_root_nofollow(root, outside)


def test_retention_audit_counts_committed_and_pending_without_unlink(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        audit_derived_receipt_retention,
    )
    service, source, _ = _library(tmp_path)
    try:
        result = ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        assert result.artifact_id != source.artifact_id
        stage = tmp_path / "library" / ".cifereca-staging" / "variants"
        committed = next(stage.glob("pending-*.json"))
        pending = stage / ("pending-" + "f" * 32 + ".json")
        pending.write_bytes(b"{}")
        expected_bytes = committed.stat().st_size + pending.stat().st_size
        report = audit_derived_receipt_retention(service, identity_id="id1")
        assert report["identity_id"] == "id1"
        assert report["count"] == 2
        assert report["statuses"]["already_committed"] == 1
        assert report["statuses"]["manual_review"] == 1
        assert report["receipt_bytes_observed"] == expected_bytes
        assert report["unknown_size_count"] == 0
        assert report["has_more"] is False
        assert report["destructive_cleanup_allowed"] is False
        assert committed.exists() and pending.exists()
        assert len(service.storage.artifact_variants()) == 1
    finally:
        service.storage.close()


def test_retention_audit_paginates_and_never_deletes_foreign_data(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        audit_derived_receipt_retention, MAX_RECEIPTS,
    )
    service, source, _ = _library(tmp_path)
    try:
        stage = tmp_path / "library" / ".cifereca-staging" / "variants"
        stage.mkdir(parents=True)
        for idx in range(MAX_RECEIPTS + 2):
            (stage / ("pending-" + f"{idx:032x}" + ".json")).write_bytes(b"{}")
        first = audit_derived_receipt_retention(service, identity_id="id1")
        assert first["count"] == MAX_RECEIPTS
        assert first["has_more"] is True
        assert first["receipt_bytes_observed"] == 2 * MAX_RECEIPTS
        second = audit_derived_receipt_retention(
            service, identity_id="id1", after_cursor=first["next_cursor"],
        )
        assert second["count"] == 2
        assert second["has_more"] is False
        assert second["receipt_bytes_observed"] == 4
        assert second["statuses"]["manual_review"] == 2
        assert len(list(stage.glob("pending-*.json"))) == MAX_RECEIPTS + 2
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_retention_audit_reports_symlink_without_following_outside(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        audit_derived_receipt_retention,
    )
    service, source, _ = _library(tmp_path)
    try:
        stage = tmp_path / "library" / ".cifereca-staging" / "variants"
        stage.mkdir(parents=True)
        foreign = tmp_path / "foreign.json"
        foreign.write_bytes(b"do not read another profile")
        journal = stage / ("pending-" + "b" * 32 + ".json")
        journal.symlink_to(foreign)
        report = audit_derived_receipt_retention(service, identity_id="id1")
        assert report["count"] == 1
        assert report["statuses"]["manual_review"] == 1
        assert report["receipt_bytes_observed"] == 0
        assert report["unknown_size_count"] == 1
        assert foreign.read_bytes() == b"do not read another profile"
        assert journal.is_symlink()
    finally:
        service.storage.close()


def test_completed_intent_compacts_only_sql_and_is_idempotent(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        compact_committed_derived_intent, _pending_intent_key,
    )
    service, source, _ = _library(tmp_path)
    try:
        child = ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        reviewed = review_pending_derived_receipts(service, identity_id="id1")
        assert len(reviewed) == 1
        assert reviewed[0]["status"] == "already_committed"
        journal = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        original = journal.read_bytes()
        key = _pending_intent_key(journal)
        assert service.storage.metadata_get(key) == reviewed[0]["sha256"]
        kwargs = dict(
            identity_id="id1",
            receipt_cursor=reviewed[0]["cursor"],
            approved_receipt_sha256=reviewed[0]["sha256"],
        )
        result = compact_committed_derived_intent(service, **kwargs)
        assert result["status"] == "compacted"
        assert result["receipt_retained"] and result["canonical_origin_retained"]
        assert result["intent_removed"]
        assert service.storage.metadata_get(key) is None
        assert journal.read_bytes() == original
        assert review_pending_derived_receipts(
            service, identity_id="id1",
        )[0]["status"] == "already_committed"
        assert compact_committed_derived_intent(
            service, **kwargs,
        )["status"] == "already_compacted"
        assert len(service.storage.artifact_variants(
            artifact_id=child.artifact_id,
        )) == 1
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM origin_links WHERE artifact_id=? "
                "AND origin_kind='derived'",
                (child.artifact_id,),
            ).fetchone()[0] == 1
    finally:
        service.storage.close()


def test_compaction_refuses_pending_or_wrong_approval_without_db_mutation(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        compact_committed_derived_intent, _pending_intent_key,
    )
    service, source, _ = _library(tmp_path)
    try:
        _failed_publication(service, source)
        reviewed = review_pending_derived_receipts(service, identity_id="id1")
        journal = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        key = _pending_intent_key(journal)
        original_intent = service.storage.metadata_get(key)
        assert reviewed[0]["status"] == "recoverable"
        for digest in (reviewed[0]["sha256"], "0" * 64):
            with pytest.raises(
                ValueError, match="derived_compaction_unattested_receipt",
            ):
                compact_committed_derived_intent(
                    service, identity_id="id1",
                    receipt_cursor=reviewed[0]["cursor"],
                    approved_receipt_sha256=digest,
                )
        assert journal.exists()
        assert service.storage.metadata_get(key) == original_intent
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


def test_compaction_refuses_missing_canonical_origin_inside_writer_lock(
    tmp_path, monkeypatch,
):
    from cifereca.core import derived_variant_recovery as recovery
    service, source, _ = _library(tmp_path)
    try:
        ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        reviewed = recovery.review_pending_derived_receipts(
            service, identity_id="id1",
        )
        journal = next((
            tmp_path / "library" / ".cifereca-staging" / "variants"
        ).glob("pending-*.json"))
        key = recovery._pending_intent_key(journal)
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM origin_links WHERE origin_kind='derived'")
        with pytest.raises(
            ValueError, match="derived_compaction_unattested_receipt",
        ):
            recovery.compact_committed_derived_intent(
                service, identity_id="id1",
                receipt_cursor=reviewed[0]["cursor"],
                approved_receipt_sha256=reviewed[0]["sha256"],
            )
        assert service.storage.metadata_get(key) == reviewed[0]["sha256"]
        assert journal.exists()
    finally:
        service.storage.close()


def test_compaction_rejects_other_active_profile(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        compact_committed_derived_intent,
    )
    service, source, _ = _library(tmp_path)
    try:
        ArtifactExecutionBrokerImpl(service).execute_transform(
            TransformRequest(source, "optimize"), control=Control(),
        )
        reviewed = review_pending_derived_receipts(service, identity_id="id1")
        service.current_identity.identity_id = "id2"
        with pytest.raises(
            ValueError, match="derived_recovery_active_identity_mismatch",
        ):
            compact_committed_derived_intent(
                service, identity_id="id1",
                receipt_cursor=reviewed[0]["cursor"],
                approved_receipt_sha256=reviewed[0]["sha256"],
            )
    finally:
        service.storage.close()



def test_bounded_retention_summary_aggregates_pages_without_deletion(tmp_path):
    from cifereca.core.derived_variant_recovery import (
        summarize_derived_receipt_retention, MAX_RECEIPTS,
    )
    service, source, _ = _library(tmp_path)
    try:
        stage = tmp_path / "library" / ".cifereca-staging" / "variants"
        stage.mkdir(parents=True, exist_ok=True)
        for idx in range(MAX_RECEIPTS + 3):
            (stage / ("pending-" + f"{idx:032x}" + ".json")).write_bytes(b"{}")
        first = summarize_derived_receipt_retention(
            service, identity_id="id1", max_pages=1,
        )
        assert first["complete"] is False
        assert first["pages_scanned"] == 1
        assert first["receipt_count_observed"] == MAX_RECEIPTS
        assert first["receipt_bytes_observed"] == MAX_RECEIPTS * 2
        assert first["statuses"]["manual_review"] == MAX_RECEIPTS
        assert first["next_cursor"]
        rest = summarize_derived_receipt_retention(
            service, identity_id="id1", after_cursor=first["next_cursor"],
        )
        assert rest["complete"] is True
        assert rest["pages_scanned"] == 1
        assert rest["receipt_count_observed"] == 3
        assert rest["next_cursor"] is None
        assert rest["destructive_cleanup_allowed"] is False
        full = summarize_derived_receipt_retention(
            service, identity_id="id1",
        )
        assert full["receipt_count_observed"] == MAX_RECEIPTS + 3
        assert full["statuses"]["manual_review"] == MAX_RECEIPTS + 3
        assert full["receipt_bytes_observed"] == 2 * (MAX_RECEIPTS + 3)
        assert len(list(stage.glob("pending-*.json"))) == MAX_RECEIPTS + 3
        assert service.storage.artifact_variants() == []
    finally:
        service.storage.close()


@pytest.mark.parametrize("bad", [0, -1, 65, "3", None, True])
def test_retention_summary_rejects_invalid_work_limits(tmp_path, bad):
    from cifereca.core.derived_variant_recovery import (
        summarize_derived_receipt_retention,
    )
    service, source, _ = _library(tmp_path)
    try:
        with pytest.raises(ValueError, match="derived_retention_invalid_page_limit"):
            summarize_derived_receipt_retention(
                service, identity_id="id1", max_pages=bad,
            )
    finally:
        service.storage.close()
