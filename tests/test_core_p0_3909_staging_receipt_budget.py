"""P0 #3909: one budget slot per exact receipt, not per SQL projection.

No provider connection or external network; the source and SQLite/FS
fixtures use one synthetic completed PDF delivery with an injected promotion
failure. Tight bounds expose early false truncation without 512 file hashes.
"""
from __future__ import annotations

import json
import pytest

from cifereca.core import acquisition_staging_evidence as evidence
from cifereca.core.acquisition_audit import inspect_exact_source, safe_acquisition_audit
from cifereca.core.canonical_recovery import _stage_id
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from tests.test_core_p0_3909_unplaced_staging_receipt_audit import (
    rig, _isolate_delivery_lock, _failed_after_delivery,
)


def _append_completed_receipts(db, *, extra_core: int, legacy: bool) -> None:
    with db._lock, db._connect() as sql:
        original, raw = sql.execute(
            "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
            "WHERE status='canonical_receipt_staged' AND artifact_id IS NULL"
        ).fetchone()
        proof = json.loads(raw)
        coords = proof["source"]
        source = SourceKey(
            ProviderRightsScope(*coords[:4]), coords[5], coords[6],
            coords[7], coords[4],
        )
        for idx in range(extra_core):
            changed = dict(proof, offer_id=f"additional-offer-{idx}",
                           receipt="core-delivery:" + f"{idx+1:064x}")
            key = _stage_id(source, changed["offer_id"])
            assert key != original
            sql.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,identity_id,provider_id,credential_id,"
                "account_id,item_id,status,artifact_id,metadata_json,"
                "created_at,updated_at) "
                "SELECT ?,identity_id,provider_id,credential_id,account_id,"
                "item_id,status,artifact_id,?,created_at,updated_at "
                "FROM acquisition_ledger WHERE acquisition_id=?",
                (key, json.dumps(changed), original),
            )
        if legacy:
            sql.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,identity_id,provider_id,credential_id,"
                "account_id,item_id,status,artifact_id,metadata_json,"
                "created_at,updated_at) "
                "SELECT 'legacy-different-item',identity_id,provider_id,"
                "credential_id,account_id,item_id,'complete',?, '{}',"
                "created_at,updated_at FROM acquisition_ledger "
                "WHERE acquisition_id=?",
                ("sha256:" + "f" * 64, original),
            )


@pytest.mark.parametrize(
    "extra_core,legacy,limit,expected_count,expected_coverage",
    [
        (0, False, 1, 1, "indexed_and_core_receipts"),
        (1, False, 2, 2, "indexed_and_core_receipts"),
        (2, False, 3, 3, "indexed_and_core_receipts"),
        (1, True, 3, 3, "indexed_and_core_receipts"),
        (3, False, 3, 0, "truncated"),
        (2, True, 3, 0, "truncated"),
    ],
)
def test_budget_counts_unique_operations_without_double_charging_core_receipts(
    rig, monkeypatch, extra_core, legacy, limit, expected_count, expected_coverage,
):
    service, storage, root, gets = _failed_after_delivery(rig, monkeypatch)
    _append_completed_receipts(storage, extra_core=extra_core, legacy=legacy)
    monkeypatch.setattr(evidence, "_MAX_ROWS", limit)
    stage = inspect_exact_source(
        storage, service.account, library_root=root, verify_files=False,
    )["staging_evidence"]
    assert stage["coverage"] == expected_coverage
    assert len(stage["observations"]) == expected_count
    assert stage["staged_verified"] == 0  # no file read without verify_files
    assert stage["staged_visible_in_library"] == 0
    assert all(not x["origin_proven_by_receipt"] for x in stage["observations"])
    if expected_count:
        assert sum(
            row["staging_state"] == "core_staged_unverified"
            for row in stage["observations"]
        ) == 1 + extra_core
        assert len(set(x["receipt_ref"] for x in stage["observations"])) == expected_count
    safe = safe_acquisition_audit(inspect_exact_source(
        storage, service.account, library_root=root, verify_files=False,
    ))
    assert safe["staging_evidence"]["coverage"] == expected_coverage
    assert storage.placements() == []
    assert gets == ["signed-right-offer"]


def test_summary_placeholder_does_not_merge_distinct_receipts(rig, monkeypatch):
    from cifereca.core.sync_report import audit_source_counts

    service, storage, root, gets = _failed_after_delivery(rig, monkeypatch)
    _append_completed_receipts(storage, extra_core=2, legacy=False)
    monkeypatch.setattr(evidence, "_MAX_ROWS", 3)
    counts = audit_source_counts(storage, service.account, library_root=root)
    assert counts["staging_evidence_coverage"] == "indexed_and_core_receipts"
    assert counts["staged_sha_verified_count"] == 3
    assert counts["staged_visible_in_library_count"] == 0
    assert counts["placement_reference_count"] == 0
    assert storage.placements() == []
    assert gets == ["signed-right-offer"]
