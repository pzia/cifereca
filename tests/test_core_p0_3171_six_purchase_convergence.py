"""P0 #3171: six Numilog purchases converge without duplicating local files.

Three existing exact receipts, two explicit local declarations, one consented
missing acquisition; the second startup consumes no delivery again.
Offline-only synthetic purchase rows, SQLite and EPUB files.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from time import time

from cifereca.core import numilog_materialization_startup as startup
from cifereca.core import library_declared_links as links
from cifereca.core.library_inventory import scan_identity_roots
from tests.test_convergence_declared_download_guard import rig, declare
from tests.test_core_p0_numilog_auto_materialization import _authorize
from tests.test_core_p0_1905_numilog_offline_preview import _owned
from tests.test_core_p0_1862_numilog_store_mirror import _row
from tests.test_library_reconciliation import epub


def test_six_purchases_three_attested_two_declared_one_download_then_noop(
    rig, tmp_path, monkeypatch,
):
    service, account, session, calls, first, first_book, scope, _options, broker = rig
    root = first_book.parents[2]
    # One existing declaration from the shared Core fixture.
    declare(rig)
    rows = [_row(account.credential_id, index) for index in range(6)]
    assert rows[0].record_id == first.record_id

    rights = {}
    for index in range(1, 6):
        item, eid = _owned(
            service, account, lid=f"lid{index}",
            variant=rows[index].record_id.rsplit(":", 1)[1],
        )
        assert item == rows[index].record_id
        rights[index] = eid

    # Second existing EPUB deliberately linked by the account holder.
    second_path = root / "numilog/acsm-existing/another.epub"
    epub(second_path, title="Explicitly chosen second book")
    observed = next(
        file for file in scan_identity_roots({"default": (root,)}).files
        if file.path == str(second_path)
    )
    proposed = dict(
        observation=observed, identity_id=account.identity_id,
        provider_id="numilog", credential_id=account.credential_id,
        account_id=account.unique_id, item_id=rows[1].record_id,
    )
    preview = links.declare_local_link(service, **proposed)
    links.declare_local_link(service, **proposed, confirmation=preview.plan_token)

    stored_paths = []
    for index in (2, 3, 4):
        path = root / "numilog" / f"acquired-{index}.epub"
        epub(path, title=f"Attested local book {index}")
        stored_paths.append(path)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = "sha256:" + digest
        service.storage.artifact_put({
            "artifact_id": artifact_id, "sha256": digest, "size": len(data),
            "media_format": "epub", "kind": "file",
        })
        service.storage.placement_put({
            "artifact_id": artifact_id, "path": str(path),
            "identity_id": account.identity_id, "provider_id": "numilog",
            "source_item_id": rows[index].record_id, "state": "present",
        })
        service.storage.acquisition_put({
            "acquisition_id": f"synthetic-complete-{index}",
            "entitlement_id": rights[index], "artifact_id": artifact_id,
            "identity_id": account.identity_id, "provider_id": "numilog",
            "credential_id": account.credential_id, "account_id": account.unique_id,
            "item_id": rows[index].record_id, "status": "complete",
        })

    _authorize(broker, broker._source_id(account))
    session.library = lambda: rows
    downloads = []

    def deliver(item, directory):
        # Only the sixth absent purchase may reach the authorized provider seam.
        assert item.record_id == rows[5].record_id
        downloads.append(item.record_id)
        path = Path(directory) / "newly-acquired.epub"
        epub(path, title="Just acquired")
        return path, ""

    session.download_direct = deliver
    monkeypatch.setattr(service.acquisition, "run_post_download", lambda *_a: None)

    first_sync = startup.execute(broker, account.identity_id, "startup")
    assert first_sync.ok and first_sync.facts["status"] == "complete"
    assert first_sync.changed == first_sync.facts["downloaded"] == 1
    assert first_sync.facts["attempted"] == 1
    assert first_sync.facts["skipped"] == 5
    assert first_sync.facts["already_local_declared"] == 2
    assert first_sync.facts["blocked"] == first_sync.facts["failed"] == 0
    assert downloads == [rows[5].record_id]
    assert len(service.storage.acquisitions(provider_id="numilog")) == 4

    monkeypatch.setattr(startup, "time", lambda: time() + 7200)
    second_sync = startup.execute(broker, account.identity_id, "startup")
    assert second_sync.ok and second_sync.facts["status"] == "complete"
    assert second_sync.changed == second_sync.facts["attempted"] == 0
    assert second_sync.facts["skipped"] == 6
    assert second_sync.facts["already_local_declared"] == 2
    assert second_sync.facts["blocked"] == second_sync.facts["failed"] == 0
    assert downloads == [rows[5].record_id]
    assert len(service.storage.acquisitions(provider_id="numilog")) == 4
    assert all(path.is_file() for path in stored_paths)
    assert first_book.is_file() and second_path.is_file()
    assert calls == []


def test_six_existing_purchases_two_receipts_repaired_without_redownload(
    rig, tmp_path, monkeypatch,
):
    """Three complete + one declared + two delivered, with independent copies."""
    import pytest
    from cifereca.acquisition_engine import stable_id

    service, account, session, calls, first, first_book, scope, _options, broker = rig
    declare(rig)
    storage = service.storage
    root = first_book.parents[2]
    rows = [_row(account.credential_id, index) for index in range(6)]
    for index in range(1, 6):
        _owned(service, account, lid=f"lid{index}",
               variant=rows[index].record_id.rsplit(":", 1)[1])
    deliveries = []

    def historical_delivery(item, directory):
        index = next(i for i, row in enumerate(rows) if row.record_id == item.record_id)
        deliveries.append(index)
        acsm_sha = hashlib.sha256(f"synthetic-acsm-{index}".encode()).hexdigest()
        path = Path(directory) / (f"acsm-{acsm_sha}" if index >= 4 else "") / f"book-{index}.epub"
        path.parent.mkdir(parents=True, exist_ok=True)
        epub(path, title=f"Delivered book {index}")
        if index >= 4:
            indexed = service.catalog_index_path(
                path, item_id=acsm_sha, identity_id=account.identity_id, provider_id="numilog",
            )
            assert indexed["status"] == "indexed"
            manual_id = "manual-acsm:" + hashlib.sha256(
                (account.identity_id + ":" + acsm_sha).encode(),
            ).hexdigest()[:32]
            storage.acquisition_put({
                "acquisition_id": manual_id, "provider_id": "numilog", "credential_id": "manual",
                "identity_id": account.identity_id, "item_id": acsm_sha, "status": "complete",
                "artifact_id": indexed["artifact_id"],
                "metadata": {"source_acsm_sha256": acsm_sha, "path": str(path)},
            })
        return path, ""

    session.download_direct = historical_delivery
    monkeypatch.setattr(service.acquisition, "run_post_download", lambda *_a: None)
    receipts, paths = {}, {}
    for index in range(1, 6):
        delivered = service.acquire_direct_item(rows[index])
        receipts[index] = delivered.metadata["numilog_checkpoint_id"]
        paths[index] = delivered.path
        if index <= 3:
            service.resume_numilog_materialization(receipts[index])
    assert deliveries == [1, 2, 3, 4, 5]

    # One pending EPUB shares bytes with two independently owned physical copies.
    independent = []
    for owner in ("alex", "third-profile"):
        storage.identity_put({"identity_id": owner, "label": owner})
        copy = root.parent / owner / "numilog" / "independent.epub"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(paths[4].read_bytes())
        indexed = service.catalog_index_path(copy, identity_id=owner,
                                             provider_id="numilog", item_id=f"edition-{owner}")
        assert indexed["status"] == "indexed"
        placed = storage.placement_for_path(copy)
        catalog = next(c for c in storage.catalog_entries() if c["placement_id"] == placed["placement_id"])
        ledger_id = f"independent-{owner}"
        storage.acquisition_put({
            "acquisition_id": ledger_id, "provider_id": "numilog", "credential_id": owner,
            "identity_id": owner, "account_id": owner, "item_id": f"edition-{owner}",
            "artifact_id": placed["artifact_id"], "status": "complete", "metadata": {"path": str(copy)},
        })
        independent.append((copy, placed, catalog, storage.acquisition_get(ledger_id)))
    # The second pending book also exists under manual/, with DIFFERENT bytes.
    manual_copy = root / "manual" / "another-version.epub"
    manual_copy.parent.mkdir(parents=True, exist_ok=True)
    epub(manual_copy, title="Another version of delivered book 5")
    assert manual_copy.read_bytes() != paths[5].read_bytes()
    indexed = service.catalog_index_path(manual_copy, identity_id=account.identity_id,
                                        provider_id="local", item_id="manual-other-version")
    assert indexed["status"] == "indexed"
    manual_placement = storage.placement_for_path(manual_copy)

    _authorize(broker, broker._source_id(account))
    session.library = lambda: rows
    session.download_direct = lambda *_a, **_k: pytest.fail("existing EPUB must not be downloaded again")
    contents = {p: p.read_bytes() for p in [first_book, *paths.values(), manual_copy,
                                          *(entry[0] for entry in independent)]}
    physical_paths = set(root.parent.rglob("*.epub"))
    rights_before = {r["entitlement_id"] for r in storage.entitlements()}
    default_ledger_ids = {r["acquisition_id"] for r in storage.acquisitions()
                          if r["identity_id"] == account.identity_id}
    # The automatic worker reports pending receipts honestly and never retries delivery.
    before = startup.execute(broker, account.identity_id, "startup")
    assert not before.ok and before.facts["status"] == "partial"
    assert before.facts["skipped"] == 4 and before.facts["blocked"] == 2
    assert before.facts["attempted"] == before.facts["downloaded"] == 0

    for index in (4, 5):
        result = service.resume_numilog_materialization(receipts[index])
        assert result.path == paths[index] and result.indexed["status"] == "indexed"
        assert storage.acquisition_get(receipts[index])["status"] == "complete"
        assert storage.placement_for_path(paths[index])["source_item_id"] == rows[index].record_id
    for offset in (7200, 14400):
        monkeypatch.setattr(startup, "time", lambda: time() + offset)
        outcome = startup.execute(broker, account.identity_id, "startup")
        assert outcome.ok and outcome.facts["status"] == "complete"
        assert outcome.facts["skipped"] == 6
        assert outcome.facts["already_local_declared"] == 1
        assert outcome.facts["attempted"] == outcome.facts["downloaded"] == outcome.changed == 0
        assert outcome.facts["blocked"] == outcome.facts["failed"] == 0
        assert {r["acquisition_id"] for r in storage.acquisitions()
                if r["identity_id"] == account.identity_id} == default_ledger_ids
        assert {r["entitlement_id"] for r in storage.entitlements()} == rights_before
        assert storage.acquisition_get(stable_id(
            "numilog-materialization", account.identity_id, account.credential_id,
            account.unique_id, first.record_id,
        )) is None  # A declaration never becomes a fabricated delivery receipt.
        for copy, placed, catalog, ledger in independent:
            assert storage.placement_get(placed["placement_id"]) == placed
            assert storage.catalog_entry_get(catalog["catalog_entry_id"]) == catalog
            assert storage.acquisition_get(ledger["acquisition_id"]) == ledger
        assert storage.placement_for_path(manual_copy) == manual_placement
        assert set(root.parent.rglob("*.epub")) == physical_paths
        for index in (4, 5):
            placement = storage.placement_for_path(paths[index])
            assert len([c for c in storage.catalog_entries()
                        if c["placement_id"] == placement["placement_id"]]) == 1
        assert all(path.read_bytes() == data for path, data in contents.items())
    assert deliveries == [1, 2, 3, 4, 5] and calls == []
