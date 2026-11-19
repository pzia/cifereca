"""#4520: observed named staging stock is not a quota refund or delete plan."""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core import derived_variant_stock_review as stock
from cifereca.core import derived_variant_journal as journal
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _root(intent):
    return Path(intent.parent_path).parent.parent


def _review(service, intent):
    return stock.review_derived_staging_stock(
        service.storage, library_root=_root(intent), identity_id=intent.identity_id,
    )


def test_linked_stage_is_counted_once_not_reclaimable(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    try:
        _publish_reserved(service.storage, intent, reservation)
        stage = Path(intent.staged_path)
        target = Path(intent.candidate_path)
        assert stage.samefile(target)
        before = journal.summarize_derived_journal(service.storage)
        result = _review(service, intent)
        assert result["observations"]["same_inode_target"] == 1
        assert result["observed_unique_inode_logical_bytes"] == 9
        assert result["observed_unique_inode_allocated_bytes"] == stage.stat().st_blocks * 512
        assert not result["reclaimable_bytes_known"]
        assert not result["complete_physical_inventory"] and not result["sha_verified"]
        assert not result["quota_refund_authorized"] and not result["destructive_cleanup_allowed"]
        assert str(stage) not in repr(result)
        assert journal.summarize_derived_journal(service.storage) == before
        assert stage.samefile(target)
    finally:
        service.storage.close()


def test_pending_duplicate_receipts_do_not_double_count_inode(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    try:
        _publish_reserved(service.storage, intent, reservation)
        target = Path(intent.candidate_path).with_name("other.epub")
        journal.reserve_derived_publication(
            service.storage, replace(intent, candidate_path=str(target)),
        )
        report = _review(service, intent)
        assert report["receipts"] == 2
        assert report["observations"]["stage_only"] == report["observations"]["same_inode_target"] == 1
        assert report["observed_unique_inode_logical_bytes"] == 9
        assert not target.exists()
    finally:
        service.storage.close()


@pytest.mark.parametrize("change", ["absent", "foreign", "symlink", "parent_unavailable"])
def test_unknown_or_absent_stage_does_not_claim_bytes(tmp_path, change):
    service, intent, reservation = _prepared_publication(tmp_path)
    try:
        stage = Path(intent.staged_path)
        if change == "parent_unavailable":
            stage.parent.rename(stage.parent.with_name("offline"))
        else:
            stage.unlink()
            if change == "foreign":
                stage.write_bytes(b"new bytes")
            elif change == "symlink":
                stage.symlink_to(intent.candidate_path)
        report = _review(service, intent)
        expected = "stage_absent" if change == "absent" else "unverified"
        assert report["observations"][expected] == 1
        assert report["observed_unique_inode_logical_bytes"] == 0
        assert not report["quota_refund_authorized"]
        assert Path(intent.candidate_path).read_bytes() == b"new bytes"
    finally:
        service.storage.close()
