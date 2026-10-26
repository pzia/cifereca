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
