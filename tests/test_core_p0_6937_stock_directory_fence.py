"""#6937: staging-stock observation honors the exact private profile binding."""

from pathlib import Path

import pytest

from cifereca.core import derived_variant_stock_review as stock
from cifereca.core import derived_variant_journal as journal
from tests.test_core_p0_4520_derived_journal import _prepared_publication


@pytest.mark.parametrize("invalid", [
    "library\n", "library\x7f", "library\x00",
    "x" * 257, ".", "..", "nested/name", "nested\\name",
])
def test_stock_review_rejects_invalid_sql_identity_directory_without_fs_changes(tmp_path, invalid):
    service, intent, reservation = _prepared_publication(tmp_path)
    storage = service.storage
    try:
        stage, final = Path(intent.staged_path), Path(intent.candidate_path)
        original_bytes = Path(intent.parent_path).read_bytes()
        stage_bytes = stage.read_bytes()
        receipt = storage.metadata_get(reservation.key)
        with storage._lock, storage._connect() as sql:
            sql.execute(
                "UPDATE identities SET directory_name=? WHERE identity_id=?",
                (invalid, intent.identity_id),
            )
        with pytest.raises(journal.DerivedJournalConflict):
            stock._snapshot(storage, intent.identity_id)
        reviewed = stock.review_derived_staging_stock(
            storage, library_root=Path(intent.parent_path).parent.parent,
            identity_id=intent.identity_id,
        )
        assert reviewed["status"] == "unavailable"
        assert not reviewed["reclaimable_bytes_known"]
        assert not reviewed["quota_refund_authorized"]
        assert not reviewed["destructive_cleanup_allowed"]
        assert storage.metadata_get(reservation.key) == receipt
        assert stage.read_bytes() == stage_bytes
        assert final.read_bytes() == stage_bytes
        assert Path(intent.parent_path).read_bytes() == original_bytes
    finally:
        storage.close()
