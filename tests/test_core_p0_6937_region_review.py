"""#6937: inspect stage regions without granting authority or following aliases."""
from dataclasses import asdict, replace
import os
from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_region_review as review
from cifereca.core.derived_variant_capacity_review import read_capacity_review
from cifereca.core.derived_variant_maintenance_review import admit_maintenance_review
from cifereca.gui.derived_variant_capacity_line import _subtitle
from tests.test_core_p0_4520_recovery_operator import _setup
from tests.test_core_p0_4520_derived_journal import _publish_reserved


def _read(db, root):
    return review.review_derived_private_region(
        db, library_root=root, identity_id="id1", explicitly_requested=True,
    )


def _protected(result):
    assert not any(result[key] for key in (
        "exclusive_region_verified", "sha_verified", "external_dependencies_verified",
        "complete_physical_inventory", "reclaimable_bytes_known",
        "retention_authorized", "quota_refund_authorized", "destructive_cleanup_allowed",
    ))


def test_active_and_committed_named_stock_and_unrecorded_nested_region(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        stage = Path(intent.staged_path)
        nested = Path(intent.parent_path).parent / "nested" / ".cifereca-staging" / "variants"
        nested.mkdir(parents=True)
        unrecorded = nested / "unrecorded.epub"
        unrecorded.write_bytes(b"untracked witness")
        alias = nested / "alias"
        alias.symlink_to(stage)
        (nested / "unknown-dir").mkdir()
        target = Path(intent.candidate_path)
        before = journal.summarize_derived_journal(db)
        observed = _read(db, tmp_path)
        assert observed["status"] == "observed" and observed["bounded_walk_complete"]
        assert observed["counts"] == {
            "pending_named": 1, "committed_named": 0, "unrecorded_regular": 1,
            "ambiguous_entries": 2, "unverified_regions": 0, "unobserved_receipts": 0,
        }
        _protected(observed)
        assert journal.summarize_derived_journal(db) == before
        _publish_reserved(db, intent, reservation)
        result = read_capacity_review(service, admit_maintenance_review(service))
        assert result["region"]["counts"]["committed_named"] == 1
        assert "1 sans reçu" in _subtitle(result)
        assert "Aucun orphelin supprimable attesté" in _subtitle(result)
        assert str(tmp_path) not in repr(result)
        assert unrecorded.read_bytes() == b"untracked witness"
        assert alias.is_symlink() and stage.samefile(target)
        assert journal.summarize_derived_journal(db)["committed_count"] == 1
    finally:
        db.close()


@pytest.mark.parametrize("change", ["replaced", "absent", "foreign", "corrupt", "region_symlink"])
def test_ambiguous_evidence_never_becomes_unrecorded_or_cleanup_candidate(tmp_path, change):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        stage = Path(intent.staged_path)
        prior = db.metadata_get(reservation.key)
        if change in ("replaced", "absent"):
            stage.unlink()
            if change == "replaced":
                stage.write_bytes(b"new bytes")
        elif change == "foreign":
            foreign = replace(intent, identity_id="another-identity",
                              parent_placement_id="another-placement")
            db.metadata_set(journal._slot(foreign), journal._encode({
                "schema": "derived.intent.v2", "nonce": "a" * 32, "intent": asdict(foreign),
            }))
        elif change == "corrupt":
            foreign = replace(intent, identity_id="another-identity",
                              parent_placement_id="another-placement")
            db.metadata_set(journal._slot(foreign), "unreadable")
        else:
            other = Path(intent.parent_path).parent / "other"
            other.mkdir()
            (other / ".cifereca-staging").symlink_to(stage.parent.parent)
        result = _read(db, tmp_path)
        _protected(result)
        assert result["counts"]["unrecorded_regular"] == 0
        if change == "corrupt":
            assert result["status"] == "unavailable"
        else:
            assert result["status"] == "observed"
            key = {"absent": "unobserved_receipts", "region_symlink": "unverified_regions"}.get(
                change, "ambiguous_entries",
            )
            assert result["counts"][key] == 1
        assert prior == db.metadata_get(reservation.key)
        assert Path(intent.candidate_path).read_bytes() == b"new bytes"
    finally:
        db.close()


@pytest.mark.parametrize("change", ["filesystem", "journal", "budget"])
def test_changed_or_incomplete_witness_discarded(tmp_path, monkeypatch, change):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        walk = review._walk
        calls = 0

        def changed(profile, stages, *, deadline):
            nonlocal calls
            result = walk(profile, stages, deadline=deadline)
            calls += 1
            if calls == 1:
                if change == "filesystem":
                    Path(intent.staged_path).with_name("late.epub").write_bytes(b"late")
                elif change == "journal":
                    db.metadata_set(reservation.key, "changed")
            return result

        if change == "budget":
            from cifereca.core import derived_variant_stage_symlinks
            monkeypatch.setattr(derived_variant_stage_symlinks, "_MAX_ENTRIES", 1)
        else:
            monkeypatch.setattr(review, "_walk", changed)
        report = _read(db, tmp_path)
        assert report["status"] in ("evidence_changed", "unavailable", "incomplete")
        assert not report["bounded_walk_complete"] and not any(report["counts"].values())
        _protected(report)
    finally:
        db.close()


def test_unrecorded_names_of_known_or_shared_inodes_are_ambiguous(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        stage, parent, target = map(Path, (
            intent.staged_path, intent.parent_path, intent.candidate_path,
        ))
        outside = parent.with_name("unrelated.pdf")
        outside.write_bytes(b"unrelated")
        aliases = [
            (stage, stage.with_name("different-stage-name.epub")),
            (parent, stage.with_name("parent-alias.epub")),
            (outside, stage.with_name("unrelated-alias.pdf")),
        ]
        for original, alias in aliases:
            os.link(original, alias)
        independent = stage.with_name("independent.epub")
        independent.write_bytes(b"independent")
        before = journal.summarize_derived_journal(db)
        receipt = db.metadata_get(reservation.key)
        result = _read(db, tmp_path)
        assert result["status"] == "observed"
        assert result["counts"] == {
            "pending_named": 1, "committed_named": 0, "unrecorded_regular": 1,
            "ambiguous_entries": 3, "unverified_regions": 0, "unobserved_receipts": 0,
        }
        _protected(result)
        assert target.samefile(stage)
        assert all(original.samefile(alias) for original, alias in aliases)
        assert independent.read_bytes() == b"independent"
        assert journal.summarize_derived_journal(db) == before
        assert db.metadata_get(reservation.key) == receipt
    finally:
        db.close()


def test_two_distinct_receipts_for_one_output_inode_are_ambiguous(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        stage = Path(intent.staged_path)
        alias = stage.with_name("second-stage.epub")
        os.link(stage, alias)
        other = replace(intent, parent_placement_id="different-placement",
                        staged_path=str(alias),
                        candidate_path=str(Path(intent.candidate_path).with_name("other.epub")))
        key = journal._slot(other)
        assert key != reservation.key
        raw = journal._encode({
            "schema": "derived.intent.v2", "nonce": "b" * 32, "intent": asdict(other),
        })
        db.metadata_set(key, raw)
        result = _read(db, tmp_path)
        assert result["status"] == "observed"
        assert result["counts"]["ambiguous_entries"] == 2
        assert result["counts"]["unrecorded_regular"] == 0
        assert result["counts"]["pending_named"] == 0
        assert result["counts"]["unobserved_receipts"] == 0
        _protected(result)
        assert stage.samefile(alias)
        assert db.metadata_get(key) == raw
    finally:
        db.close()


@pytest.mark.parametrize("unsafe", ["id1\x85", "id1\u202e", "id1\u2028", "id1\u200d"])
def test_region_review_refuses_invisible_identity_before_fs_or_sql(
    tmp_path, monkeypatch, unsafe,
):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        stage = Path(intent.staged_path)
        before = stage.read_bytes(), db.metadata_get(reservation.key)
        monkeypatch.setattr(review, "_open_root",
                            lambda *_a: pytest.fail("Unsafe identity must not open root"))
        monkeypatch.setattr(review, "_snapshot",
                            lambda *_a: pytest.fail("Unsafe identity must not read SQL"))
        result = review.review_derived_private_region(
            db, library_root=tmp_path, identity_id=unsafe,
            explicitly_requested=True,
        )
        assert result["status"] == "unavailable"
        _protected(result)
        assert (stage.read_bytes(), db.metadata_get(reservation.key)) == before
    finally:
        db.close()
