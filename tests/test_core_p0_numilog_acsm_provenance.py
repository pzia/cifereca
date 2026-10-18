"""#2112: Numilog's ADEPT ACSM hash is not the owned edition provenance.

Real Storage, LocalCatalog, receipt and SHA checks; synthetic EPUBs only.
The original manual-acsm ledger must independently attest the same bytes.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_fault_isolation import _write_epub


@pytest.fixture
def acsm_case(case):
    source_sha = "a" * 64
    service = case.service
    account = case.account
    manual_id = "manual-acsm:" + hashlib.sha256(
        (account.identity_id + ":" + source_sha).encode(),
    ).hexdigest()[:32]
    calls = []
    def deliver(item, root):
        calls.append(item.record_id)
        destination = Path(root) / ("acsm-" + source_sha) / "owned.epub"
        destination.parent.mkdir(exist_ok=True, parents=True)
        _write_epub(destination)
        indexed = service.catalog_index_path(
            destination, title="Synthetic owned book",
            item_id=source_sha, provider_id="numilog",
            identity_id=account.identity_id,
        )
        assert indexed["status"] == "indexed"
        service.storage.acquisition_put({
            "acquisition_id": manual_id,
            "provider_id": "numilog",
            "credential_id": "manual",
            "identity_id": account.identity_id,
            "item_id": source_sha,
            "status": "complete",
            "artifact_id": indexed["artifact_id"],
            "metadata": {
                "source_acsm_sha256": source_sha, "path": str(destination),
            },
        })
        return destination, "https://private.invalid?auth=SIGNED_PRIVATE"
    case.session.download_direct = deliver
    case.source_sha = source_sha
    case.manual_id = manual_id
    case.deliveries = calls
    return case


def _delivered(case):
    acquired = case.service.acquire_direct_item(case.item)
    receipt_id = acquired.metadata["numilog_checkpoint_id"]
    path = acquired.path
    assert path.is_file()
    placed = case.service.storage.placement_for_path(path)
    assert placed["source_item_id"] == case.source_sha
    return acquired, receipt_id, path


def test_exact_numilog_acsm_delivery_reconciles_existing_file_and_two_scans(
    acsm_case,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    service = case.service
    before = path.read_bytes()
    initial = service.storage.placement_for_path(path)
    assert initial["source_item_id"] == case.source_sha
    assert initial["artifact_id"].startswith("sha256:")
    assert initial["provider_id"] == "numilog"

    result = service.resume_numilog_materialization(receipt_id)
    assert result.indexed["status"] == "indexed"
    assert result.path == path
    placed = service.storage.placement_for_path(path)
    assert placed["source_item_id"] == case.item.record_id
    assert placed["placement_id"] == initial["placement_id"]
    entries = [
        x for x in service.storage.catalog_entries()
        if x["placement_id"] == placed["placement_id"]
    ]
    assert len(entries) == 1 and entries[0]["item_id"] == case.item.record_id
    assert service.storage.acquisition_get(receipt_id)["status"] == "complete"
    assert len(case.deliveries) == 1
    for _ in range(2):
        service.catalog_scan()
        reread = service.storage.placement_for_path(path)
        assert reread["source_item_id"] == case.item.record_id
        assert reread["artifact_id"] == initial["artifact_id"]
        rows = [x for x in service.storage.catalog_entries()
                if x["placement_id"] == placed["placement_id"]]
        assert len(rows) == 1 and rows[0]["item_id"] == case.item.record_id
        assert path.read_bytes() == before
    assert len(case.deliveries) == 1


@pytest.mark.parametrize("mutation", [
    "missing_manual_ledger", "manual_foreign_owner",
    "manual_wrong_artifact", "manual_wrong_acsm",
    "placement_foreign_owner", "catalog_wrong_purchase",
    "second_catalog_row", "foreign_artifact_placement",
])
def test_ambiguous_acsm_provenance_stays_pending_without_network(
    acsm_case, mutation,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    service = case.service
    storage = service.storage
    placed = storage.placement_for_path(path)
    if mutation == "missing_manual_ledger":
        with storage._connect() as db:
            db.execute("DELETE FROM acquisition_ledger WHERE acquisition_id=?",
                       (case.manual_id,))
    elif mutation == "manual_foreign_owner":
        with storage._connect() as db:
            db.execute("UPDATE acquisition_ledger SET identity_id='foreign' "
                       "WHERE acquisition_id=?", (case.manual_id,))
    elif mutation == "manual_wrong_artifact":
        with storage._connect() as db:
            db.execute("UPDATE acquisition_ledger SET artifact_id='sha256:wrong' "
                       "WHERE acquisition_id=?", (case.manual_id,))
    elif mutation == "manual_wrong_acsm":
        manual = storage.acquisition_get(case.manual_id)
        storage.acquisition_update_state(
            case.manual_id, "complete",
            metadata={"source_acsm_sha256": "f" * 64},
        )
    elif mutation == "placement_foreign_owner":
        with storage._connect() as db:
            db.execute("UPDATE placements SET identity_id='foreign' "
                       "WHERE placement_id=?", (placed["placement_id"],))
    elif mutation == "catalog_wrong_purchase":
        with storage._connect() as db:
            db.execute("UPDATE catalog_entries SET item_id='foreign-purchase' "
                       "WHERE placement_id=?", (placed["placement_id"],))
    elif mutation == "second_catalog_row":
        storage.catalog_entry_put({
            "catalog_entry_id": "foreign-second-entry",
            "artifact_id": placed["artifact_id"],
            "placement_id": placed["placement_id"],
            "identity_id": case.account.identity_id,
            "provider_id": "numilog",
            "item_id": "other-purchase", "local_state": "local",
            "title": "Unrelated title",
        })
    else:
        foreign = path.parent / "other.epub"
        _write_epub(foreign)
        storage.placement_put({
            "placement_id": "foreign-second-placement",
            "artifact_id": placed["artifact_id"], "path": str(foreign),
            "identity_id": "foreign", "provider_id": "numilog",
            "source_item_id": "different-purchase", "state": "present",
        })
    before_bytes = path.read_bytes()
    with pytest.raises(ProviderConfigurationError):
        service.resume_numilog_materialization(receipt_id)
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert storage.placement_for_path(path)["source_item_id"] == case.source_sha
    assert path.read_bytes() == before_bytes
    assert len(case.deliveries) == 1


def test_rejected_sql_catalog_update_rolls_back_placement_then_offline_resume(
    acsm_case,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    service = case.service
    storage = service.storage
    with storage._connect() as db:
        db.execute(
            "CREATE TRIGGER block_owned_acsm_catalog BEFORE UPDATE OF item_id "
            "ON catalog_entries WHEN NEW.item_id<>OLD.item_id "
            "BEGIN SELECT RAISE(ABORT,'injected_catalog_failure'); END"
        )
    before_bytes = path.read_bytes()
    with pytest.raises(ProviderConfigurationError):
        service.resume_numilog_materialization(receipt_id)
    assert storage.placement_for_path(path)["source_item_id"] == case.source_sha
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert len(case.deliveries) == 1
    with storage._connect() as db:
        db.execute("DROP TRIGGER block_owned_acsm_catalog")
    result = service.resume_numilog_materialization(receipt_id)
    assert result.indexed["status"] == "indexed"
    assert storage.placement_for_path(path)["source_item_id"] == case.item.record_id
    assert path.read_bytes() == before_bytes
    assert len(case.deliveries) == 1


@pytest.mark.parametrize("mutation", ["tamper_bytes", "symlink_external"])
def test_changed_acsm_output_bytes_or_symlink_never_adopts_purchase(
    acsm_case, tmp_path, mutation,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    storage = case.service.storage
    if mutation == "tamper_bytes":
        path.write_bytes(b"modified")
    else:
        target = tmp_path / "untrusted.epub"
        _write_epub(target)
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(ProviderConfigurationError):
        case.service.resume_numilog_materialization(receipt_id)
    assert storage.placement_for_path(path)["source_item_id"] == case.source_sha
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert len(case.deliveries) == 1
