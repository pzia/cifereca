"""#3909: Core staging receipts without a Placement are observable, not adopted.

End-to-end Core Sync against a synthetic PDF and SQLite, without provider I/O.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cifereca.core import canonical_promotion
from cifereca.core.acquisition_audit import inspect_exact_source, safe_acquisition_audit
from cifereca.core.sync_report import audit_source_counts
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3909_atomic_promotion import rig, _drive
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES


def _failed_after_delivery(rig, monkeypatch):
    service, db, root, gets = _drive(rig)
    original = canonical_promotion.os.link

    def fail_promotion(source, target, **kwargs):
        if str(source).startswith(".canonical-promote-"):
            raise OSError("PRIVATE_URL_WITH_ACCOUNT_TOKEN_123")
        return original(source, target, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(canonical_promotion.os, "link", fail_promotion)
        result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["failed"] == 1
    assert gets == ["signed-right-offer"]
    assert db.placements() == []
    return service, db, root, gets


def _scan(service, db, root, *, verify=True):
    return inspect_exact_source(
        db, service.account, library_root=root, verify_files=verify,
    )["staging_evidence"]


def test_unplaced_core_receipt_is_visible_without_pretending_publication(rig, monkeypatch):
    service, db, root, gets = _failed_after_delivery(rig, monkeypatch)
    staged = list((root / "A" / ".canonical-delivery").glob("run-*/received.pdf"))
    assert len(staged) == 1 and staged[0].read_bytes() == BYTES

    audit = _scan(service, db, root)
    assert audit["coverage"] == "indexed_and_core_receipts"
    assert audit["staged_verified"] == 1
    assert audit["staged_visible_in_library"] == 0
    item, = audit["observations"]
    assert item["staging_state"] == "core_staged_present_unpublished"
    assert item["staging_sha_verified"] is True
    assert item["origin_proven_by_receipt"] is False
    assert len(item["receipt_ref"]) == 64
    assert db.placements() == []  # zero Canonical B

    safe = safe_acquisition_audit(inspect_exact_source(
        db, service.account, library_root=root, verify_files=True,
    ))
    entry, = safe["staging_evidence"]["observations"]
    assert entry["staging_state"] == "core_staged_present_unpublished"
    assert entry["staging_sha_verified"] is True
    rendered = json.dumps(safe)
    for private in (str(root), str(staged[0]), "signed-right-offer",
                    "card", "PRIVATE_URL_WITH_ACCOUNT_TOKEN_123"):
        assert private not in rendered

    counts = audit_source_counts(
        db, service.account, library_root=root, verify_files=True,
    )
    assert counts["staged_sha_verified_count"] == 1
    assert counts["placement_reference_count"] == 0
    assert gets == ["signed-right-offer"]


def test_unverified_mode_never_hashes_or_claims_recoverable_file(rig, monkeypatch):
    service, db, root, _ = _failed_after_delivery(rig, monkeypatch)
    item, = _scan(service, db, root, verify=False)["observations"]
    assert item["staging_state"] == "core_staged_unverified"
    assert item["staging_sha_verified"] is False


@pytest.mark.parametrize("fault", [
    "deleted", "symlink", "invalid_source", "wrong_sha", "outside_root",
    "reused_offer", "duplicate_json_key",
])
def test_untrusted_receipts_or_bytes_never_become_verified_core_staging(
    rig, monkeypatch, fault,
):
    service, db, root, _ = _failed_after_delivery(rig, monkeypatch)
    path, = (root / "A" / ".canonical-delivery").glob("run-*/received.pdf")
    if fault == "deleted":
        path.unlink()
    elif fault == "symlink":
        path.unlink()
        external = root.parent / "foreign.pdf"
        external.write_bytes(BYTES)
        path.symlink_to(external)
    else:
        with db._lock, db._connect() as sql:
            record_id, raw = sql.execute(
                "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
                "WHERE status='canonical_receipt_staged'",
            ).fetchone()
            if fault == "duplicate_json_key":
                updated = raw[:-1] + ',"path":"foreign"}'
            else:
                payload = json.loads(raw)
                if fault == "invalid_source":
                    payload["source"][3] = "different-borrower"
                elif fault == "wrong_sha":
                    payload["sha256"] = "f" * 64
                elif fault == "outside_root":
                    payload["path"] = str(root.parent / "foreign.pdf")
                else:
                    payload["offer_id"] = "unrelated-offer"
                updated = json.dumps(payload)
            sql.execute(
                "UPDATE acquisition_ledger SET metadata_json=? "
                "WHERE acquisition_id=?", (updated, record_id),
            )
    item, = _scan(service, db, root)["observations"]
    assert item["staging_sha_verified"] is False
    assert item["origin_proven_by_receipt"] is False
    assert item["staging_state"] == (
        "core_staged_missing" if fault == "deleted"
        else "core_staged_untrusted"
    )
    assert db.placements() == []


def test_receipt_counts_remain_distinct_when_report_uses_one_placeholder(
    rig, monkeypatch,
):
    from cifereca.core.canonical_recovery import _stage_id
    from cifereca.core.source_convergence import SourceKey
    from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope

    service, db, root, _ = _failed_after_delivery(rig, monkeypatch)
    with db._lock, db._connect() as sql:
        old_id, raw = sql.execute(
            "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
            "WHERE status='canonical_receipt_staged'",
        ).fetchone()
        payload = json.loads(raw)
        coords = payload["source"]
        key = SourceKey(
            ProviderRightsScope(*coords[:4]),
            coords[5], coords[6], coords[7], coords[4],
        )
        payload["offer_id"] = "second-synthetic-offer"
        payload["receipt"] = "core-delivery:" + "a" * 64
        second_id = _stage_id(key, payload["offer_id"])
        assert second_id != old_id
        sql.execute(
            "INSERT INTO acquisition_ledger("
            "acquisition_id,identity_id,provider_id,credential_id,"
            "account_id,item_id,status,metadata_json,created_at,updated_at) "
            "SELECT ?,identity_id,provider_id,credential_id,account_id,"
            "item_id,status,?,created_at,updated_at "
            "FROM acquisition_ledger WHERE acquisition_id=?",
            (second_id, json.dumps(payload), old_id),
        )

    stage = _scan(service, db, root)
    assert stage["staged_verified"] == 2
    assert len(stage["observations"]) == 2
    report = audit_source_counts(
        db, service.account, library_root=root, verify_files=True,
    )
    # audit_source_counts intentionally passes the same private placeholder
    # for every receipt. Internal dedup MUST use the operation ID instead.
    assert report["staged_sha_verified_count"] == 2
    assert report["staged_visible_in_library_count"] == 0
    assert db.placements() == []


def test_committed_b_with_unfinished_receipt_never_claims_unpublished(
    rig, monkeypatch,
):
    import cifereca.core.convergence_execution as executor

    service, db, root, requests = _drive(rig)

    def refuse(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_TOKEN")

    monkeypatch.setattr(executor, "mark_completed_delivery_published", refuse)
    monkeypatch.setattr(executor, "cleanup_published_staging", refuse)
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 1
    assert requests == ["signed-right-offer"]
    assert len(db.placements()) == 1
    stage = _scan(service, db, root)
    assert stage["staged_verified"] == 1
    item, = stage["observations"]
    assert item["staging_state"] == "core_staged_origin_recorded"
    assert item["staging_sha_verified"] is True
    assert item["origin_proven_by_receipt"] is False


def test_failed_promotion_then_actual_recovery_is_not_staged_unpublished(
    rig, monkeypatch,
):
    service, db, root, gets = _failed_after_delivery(rig, monkeypatch)
    recovered = synchronize_account_source(service, service.account)
    assert recovered["canonical_execution"]["recovered"] == 1
    assert gets == ["signed-right-offer"]
    assert len(db.placements()) == 1
    states = [x["staging_state"] for x in _scan(service, db, root)["observations"]]
    assert "core_staged_present_unpublished" not in states
