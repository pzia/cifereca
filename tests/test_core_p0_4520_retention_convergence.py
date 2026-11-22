"""#4520: exact correlated retention evidence; synthetic only."""
from dataclasses import replace
import hashlib
import os
import pytest
from pathlib import Path

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_retention_convergence as review
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _review(db, root, **kwargs):
    return review.review_converged_derived_retention(
        db, library_root=root, identity_id="id1", explicitly_requested=True, **kwargs,
    )


def test_sql_and_physical_witness_cannot_be_borrowed_across_receipts(tmp_path):
    service, intent, first = _prepared_publication(tmp_path)
    db = service.storage
    try:
        placement = _publish_reserved(db, intent, first)
        stage = Path(intent.staged_path).with_name("second-stage.epub")
        target = Path(intent.candidate_path).with_name("second.epub")
        stage.write_bytes(b"second derivative")
        info = stage.stat()
        other = replace(intent, staged_path=str(stage), candidate_path=str(target),
                        output_sha256=hashlib.sha256(stage.read_bytes()).hexdigest(),
                        output_size=info.st_size,
                        output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                        options_fingerprint="second")
        second = journal.reserve_derived_publication(db, other)
        os.link(stage, target)
        _publish_reserved(db, other, second)
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE placements SET state='missing' WHERE placement_id=?", (placement,))
        stage.unlink()
        stage.write_bytes(target.read_bytes())
        before = journal.summarize_derived_journal(db)
        result = _review(db, tmp_path)
        assert result["status"] == "observed" and result["checked"] == 2
        assert result["counts"]["canonical_sql_unverified"] == 1
        assert result["counts"]["physical_unverified"] == 1
        assert result["counts"]["linked_pair_observed"] == 0
        assert not any(result[k] for k in (
            "retention_authorized", "quota_refund_authorized", "destructive_cleanup_allowed",
            "all_dependencies_verified", "reclaimable_bytes_known",
        ))
        assert first.receipt_sha256 not in repr(result)
        assert str(tmp_path) not in repr(result)
        assert journal.summarize_derived_journal(db) == before
    finally:
        db.close()


def test_positive_witness_is_observation_only_and_shared_stage_blocks(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        before = journal.summarize_derived_journal(db)
        result = _review(db, tmp_path)
        assert result["counts"]["linked_pair_observed"] == 1
        assert not result["retention_authorized"]
        assert journal.summarize_derived_journal(db) == before
        journal.reserve_derived_publication(db, replace(
            intent, options_fingerprint="new",
            candidate_path=str(Path(intent.candidate_path).with_name("next.epub")),
        ))
        result = _review(db, tmp_path)
        assert result["counts"]["known_dependency"] == 1
        assert result["counts"]["linked_pair_observed"] == 0
    finally:
        db.close()


def test_change_during_physical_observation_discards_page(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        placement = _publish_reserved(db, intent, reservation)
        original = review._observe

        def damaged(intent, profile, current):
            with db._lock, db._connect() as sql:
                sql.execute("UPDATE placements SET state='missing' WHERE placement_id=?", (placement,))
            return original(intent, profile, current)

        monkeypatch.setattr(review, "_observe", damaged)
        result = _review(db, tmp_path)
        assert result["status"] == "evidence_changed"
        assert result["checked"] == 0 and not any(result["counts"].values())
    finally:
        db.close()


def test_budget_and_existing_transaction_do_not_claim_completion(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    sql = db._connect()
    try:
        _publish_reserved(db, intent, reservation)
        monkeypatch.setattr(review, "_MAX_BYTES", 1)
        result = _review(db, tmp_path)
        assert result["committed_receipts"] == 1 and result["checked"] == 0
        assert not result["complete_committed_page"]
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("test.retention.convergence", "private"))
        assert _review(db, tmp_path)["status"] == "unavailable"
        assert sql.in_transaction
        sql.rollback()
        assert db.metadata_get("test.retention.convergence") is None
    finally:
        if sql.in_transaction:
            sql.rollback()
        db.close()


def test_local_symlink_observation_is_correlated_before_hash(tmp_path, monkeypatch):
    service, intent, first = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, first)
        stage = Path(intent.staged_path).with_name("second-stage.epub")
        target = Path(intent.candidate_path).with_name("second.epub")
        stage.write_bytes(b"second derivative")
        info = stage.stat()
        other = replace(intent, staged_path=str(stage), candidate_path=str(target),
                        output_sha256=hashlib.sha256(stage.read_bytes()).hexdigest(),
                        output_size=info.st_size,
                        output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                        options_fingerprint="second")
        second = journal.reserve_derived_publication(db, other)
        os.link(stage, target)
        _publish_reserved(db, other, second)
        alias = Path(intent.parent_path).parent / "alias"
        alias.symlink_to(intent.staged_path)

        def no_hash(*args):
            raise AssertionError("an alias observation must block before hashing")

        monkeypatch.setattr(review, "_observe", no_hash)
        report = _review(db, tmp_path)
        assert report["status"] == "observed" and report["checked"] == 2
        assert report["counts"]["known_symlink_dependency"] == 1
        assert report["counts"]["symlink_dependency_unverified"] == 1
        assert report["counts"]["linked_pair_observed"] == 0
        assert not report["retention_authorized"]
    finally:
        db.close()


def test_symlink_added_during_hash_discards_whole_page(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        original = review._observe

        def changed(intent, profile, current):
            (profile / "late-alias").symlink_to(intent.staged_path)
            return original(intent, profile, current)

        monkeypatch.setattr(review, "_observe", changed)
        report = _review(db, tmp_path)
        assert report["status"] == "evidence_changed"
        assert report["checked"] == 0 and not any(report["counts"].values())
    finally:
        db.close()


def test_incomplete_symlink_walk_never_claims_a_positive_page(tmp_path, monkeypatch):
    from cifereca.core import derived_variant_stage_symlinks as symlinks
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        monkeypatch.setattr(symlinks, "_MAX_ENTRIES", 1)
        report = _review(db, tmp_path)
        assert report["status"] == "incomplete"
        assert report["checked"] == 0 and not any(report["counts"].values())
        assert not report["complete_committed_page"]
    finally:
        db.close()


def test_cross_profile_alias_blocks_exact_receipt_before_hash(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        sibling = tmp_path / "other-profile"
        sibling.mkdir()
        (sibling / "alias").symlink_to(os.path.relpath(intent.staged_path, sibling))

        def no_hash(*args):
            raise AssertionError("cross-profile alias must block before hashing")

        monkeypatch.setattr(review, "_observe", no_hash)
        result = _review(db, tmp_path)
        assert result["status"] == "observed"
        assert result["counts"]["known_symlink_dependency"] == 1
        assert result["counts"]["linked_pair_observed"] == 0
        assert not result["all_dependencies_verified"]
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_root_level_alias_and_unresolved_sibling_link_are_conservative(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        alias = tmp_path / "root-alias"
        alias.symlink_to(Path(intent.staged_path).parent, target_is_directory=True)
        assert _review(db, tmp_path)["counts"]["known_symlink_dependency"] == 1
        alias.unlink()
        sibling = tmp_path / "other-profile"
        sibling.mkdir()
        (sibling / "unresolved").symlink_to("unknown-chain")
        result = _review(db, tmp_path)
        assert result["counts"]["symlink_dependency_unverified"] == 1
        assert not result["retention_authorized"]
    finally:
        db.close()


def test_cross_profile_alias_created_during_hash_discards_page(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        sibling = tmp_path / "other-profile"
        sibling.mkdir()
        original = review._observe

        def changed(intent, profile, current):
            (sibling / "late-alias").symlink_to(intent.staged_path)
            return original(intent, profile, current)

        monkeypatch.setattr(review, "_observe", changed)
        result = _review(db, tmp_path)
        assert result["status"] == "evidence_changed"
        assert result["checked"] == 0 and not any(result["counts"].values())
    finally:
        db.close()


def test_full_root_budget_is_not_reset_per_profile(tmp_path, monkeypatch):
    from cifereca.core import derived_variant_stage_symlinks as symlinks
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        for i in range(3):
            folder = tmp_path / ("other-" + str(i))
            folder.mkdir()
            (folder / "ordinary").write_bytes(b"fixture")
        monkeypatch.setattr(symlinks, "_MAX_ENTRIES", 3)
        result = _review(db, tmp_path)
        assert result["status"] == "incomplete"
        assert not result["complete_committed_page"]
        assert not result["retention_authorized"]
    finally:
        db.close()


def test_exact_retention_reviews_later_receipt_and_global_dependencies(tmp_path):
    service, intent, first = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, first)
        receipts = [(first, intent)]
        for i in range(1, 6):
            stage = Path(intent.staged_path).with_name(f"stage-{i}.epub")
            target = Path(intent.candidate_path).with_name(f"variant-{i}.epub")
            stage.write_bytes(f"distinct derivative {i}".encode())
            info = stage.stat()
            other = replace(intent, staged_path=str(stage), candidate_path=str(target),
                            output_sha256=hashlib.sha256(stage.read_bytes()).hexdigest(),
                            output_size=info.st_size,
                            output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                            options_fingerprint=f"recipe-{i}")
            reservation = journal.reserve_derived_publication(db, other)
            os.link(stage, target)
            _publish_reserved(db, other, reservation)
            receipts.append((reservation, other))
        selected, chosen = max(receipts, key=lambda pair: pair[0].receipt_sha256)
        before = journal.summarize_derived_journal(db)
        overview = _review(db, tmp_path)
        assert overview["checked"] == 4 and overview["committed_receipts"] == 6
        assert not overview["complete_committed_page"]
        def exact(**kwargs):
            return review.review_exact_derived_retention(
                db, library_root=tmp_path, identity_id="id1",
                receipt_sha256=selected.receipt_sha256, explicitly_requested=True, **kwargs)
        result = exact()
        assert result["checked"] == result["committed_receipts"] == 1
        assert result["counts"]["linked_pair_observed"] == 1
        assert selected.receipt_sha256 not in repr(result) and str(tmp_path) not in repr(result)
        assert not any(result[k] for k in ("retention_authorized", "quota_refund_authorized",
                                          "destructive_cleanup_allowed", "all_dependencies_verified"))
        assert journal.summarize_derived_journal(db) == before
        # A dependency on the selected later receipt must not be missed
        # simply because the default overview only scans its first four.
        db.metadata_set("test.later.consumer", chosen.staged_path)
        assert exact()["counts"]["known_dependency"] == 1
        assert _review(db, tmp_path)["counts"]["known_dependency"] == 0
        assert journal.summarize_derived_journal(db) == before
        assert Path(chosen.staged_path).samefile(chosen.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("case", ["unconfirmed", "absent", "invalid", "foreign", "nested", "late_alias"])
def test_exact_retention_refuses_changed_or_unadmitted_receipt(tmp_path, monkeypatch, case):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    sql = db._connect()
    try:
        _publish_reserved(db, intent, reservation)
        args = dict(library_root=tmp_path, identity_id="id1",
                    receipt_sha256=reservation.receipt_sha256, explicitly_requested=True)
        if case == "unconfirmed":
            args["explicitly_requested"] = False
        elif case == "absent":
            args["receipt_sha256"] = "0" * 64
        elif case == "invalid":
            args["receipt_sha256"] = True
        elif case == "foreign":
            args["identity_id"] = "other"
        elif case == "nested":
            sql.execute("BEGIN IMMEDIATE")
            sql.execute("INSERT INTO metadata(key,value) VALUES('test.caller','pending')")
        else:
            original = review._observe
            def changed(intent, profile, current):
                (profile / "alias").symlink_to(intent.staged_path)
                return original(intent, profile, current)
            monkeypatch.setattr(review, "_observe", changed)
        result = review.review_exact_derived_retention(db, **args)
        assert result["status"] == ("evidence_changed" if case == "late_alias" else "unavailable")
        assert result["checked"] == 0 and not any(result["counts"].values())
        assert not result["retention_authorized"]
        if case == "nested":
            assert sql.in_transaction
            assert sql.execute("SELECT value FROM metadata WHERE key='test.caller'").fetchone() == ("pending",)
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        if sql.in_transaction:
            sql.rollback()
        db.close()
