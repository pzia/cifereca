"""P0 #3171: exact, safe reason for a blocked offline Numilog index resume.

All purchases/EPUB bytes, receipts and accounts are synthetic. Real SQLite,
same locks/provenance; never downloads, calls a provider or moves user files.
"""
import pytest

from cifereca.core.numilog_materialization_checkpoint import (
    NumilogIndexResumeRejected,
)
from tests.test_core_p0_numilog_acsm_provenance import acsm_case, _delivered
from tests.test_numilog_owned_library_download import case  # fixture for acsm_case


@pytest.mark.parametrize(("fault", "reason"), [
    ("manual_receipt_missing", "manual_receipt_missing"),
    ("manual_wrong_owner", "manual_receipt_mismatch"),
    ("catalogue_conflict", "catalogue_conflict"),
    ("shared_artifact_elsewhere", "artifact_shared_elsewhere"),
    ("competing_purchase_receipt", "competing_receipts"),
])
def test_atomic_refusal_identifies_failed_gate_without_changing_evidence(
    acsm_case, tmp_path, fault, reason,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    storage = case.service.storage
    placement = storage.placement_for_path(path)
    original = path.read_bytes()

    if fault == "manual_receipt_missing":
        with storage._connect() as db:
            db.execute("DELETE FROM acquisition_ledger WHERE acquisition_id=?",
                       (case.manual_id,))
    elif fault == "manual_wrong_owner":
        with storage._connect() as db:
            db.execute("UPDATE acquisition_ledger SET identity_id='other' "
                       "WHERE acquisition_id=?", (case.manual_id,))
    elif fault == "catalogue_conflict":
        storage.catalog_entry_put({
            "catalog_entry_id": "synthetic-second-row",
            "artifact_id": placement["artifact_id"],
            "placement_id": placement["placement_id"],
            "identity_id": case.account.identity_id,
            "provider_id": "numilog", "item_id": "other-item",
            "local_state": "local",
        })
    elif fault == "shared_artifact_elsewhere":
        # Same content hash, a different identity and an independent path.
        # This is a genuine ambiguity: DO NOT silently merge ownership.
        another = tmp_path / "another-copy.epub"
        another.write_bytes(original)
        storage.placement_put({
            "placement_id": "independent-placement",
            "artifact_id": placement["artifact_id"], "path": str(another),
            "identity_id": "foreign", "provider_id": "numilog",
            "source_item_id": "different-edition", "state": "present",
        })
    else:
        row = storage.acquisition_get(receipt_id)
        storage.acquisition_put({**row, "acquisition_id": "other-purchase-receipt"})

    # Preserve the old bool API; enhanced refusal comes from the SAME
    # SQLite transaction, without a speculative second provenance scan.
    assert storage.numilog_checkpoint_adopt_acsm_placement(
        receipt_id, expected_path=str(path),
    ) is False
    assert storage.numilog_checkpoint_adopt_acsm_placement(
        receipt_id, expected_path=str(path), diagnose=True,
    ) == reason

    with pytest.raises(NumilogIndexResumeRejected) as raised:
        case.service.resume_numilog_materialization(receipt_id)
    assert raised.value.reason_code == reason
    msg = str(raised.value)
    assert "PRIVATE" not in msg and str(path) not in msg
    assert receipt_id not in msg and case.item.record_id not in msg
    assert path.read_bytes() == original
    assert storage.placement_for_path(path)["source_item_id"] == case.source_sha
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert case.deliveries == [case.item.record_id]


def test_atomic_success_retains_exact_receipt_and_zero_new_network(acsm_case):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    storage = case.service.storage
    assert storage.numilog_checkpoint_adopt_acsm_placement(
        receipt_id, expected_path=str(path), diagnose=True,
    ) == "adopted"
    result = case.service.resume_numilog_materialization(receipt_id)
    assert result.path == path
    assert result.indexed["status"] == "indexed"
    assert storage.acquisition_get(receipt_id)["status"] == "complete"
    assert case.deliveries == [case.item.record_id]


@pytest.mark.parametrize(("raw", "expected"), [
    ("private/unexpected", "adoption_unavailable"),
    ({"path": "/private/"}, "adoption_unavailable"),
    ("artifact_shared_elsewhere", "artifact_shared_elsewhere"),
])
def test_refusal_codes_are_an_exact_allowlist(raw, expected):
    refusal = NumilogIndexResumeRejected(raw)
    assert refusal.reason_code == expected
    assert "private" not in str(refusal).lower()
