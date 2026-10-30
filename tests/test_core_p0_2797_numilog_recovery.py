"""#2797/#3171: real Core/SQLite Numilog legacy recovery, no network."""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from cifereca.core.numilog_manifest_rights import mirror_numilog_manifest_rights
from cifereca.core.numilog_materialization_startup import execute
from cifereca.core.numilog_recovery import recovery_gate, recovery_id
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row


def epub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", "<html><body>same SHA, distinct purchases</body></html>")


def setup_six(tmp_path, monkeypatch):
    service, account, session, _calls, broker, sid = _setup(tmp_path)
    _authorize(broker, sid)
    rows = [_row(account.credential_id, i) for i in range(6)]
    session.library = lambda: list(rows)
    assert mirror_numilog_manifest_rights(
        service, rows, identity_id=account.identity_id,
        credential_id=account.credential_id, account_id=account.unique_id,
    ) == 6
    rights = {r["item_id"]: r["entitlement_id"]
              for r in service.storage.entitlements(provider_id="numilog")}
    assert len(rights) == 6
    root = service.library_identity_provider_dir(account.identity_id, "numilog")
    monkeypatch.setattr(service.acquisition, "run_post_download", lambda *_a: None)
    return service, account, session, broker, root, rows, rights


def receipt(service, account, row, right, i, *, path=None, status="complete"):
    artifact = None
    if path is not None:
        epub(path)
        indexed = service.catalog_index_path(
            path, title=row.title, item_id=row.record_id,
            identity_id=account.identity_id, provider_id="numilog",
        )
        assert indexed["status"] == "indexed"
        artifact = indexed["artifact_id"]
    service.storage.acquisition_put({
        "acquisition_id": "legacy-numilog-" + str(i),
        "provider_id": "numilog", "credential_id": account.credential_id,
        "account_id": account.unique_id, "identity_id": account.identity_id,
        "item_id": row.record_id, "entitlement_id": right,
        "artifact_id": artifact, "status": status,
    })


@pytest.mark.parametrize("nested", [False, True])
def test_six_verified_purchases_zero_get_independent_of_folders(
    tmp_path, monkeypatch, nested,
):
    service, account, session, broker, root, rows, rights = setup_six(tmp_path, monkeypatch)
    try:
        for i, row in enumerate(rows):
            path = (root / ("acsm-" + hashlib.sha256(str(i).encode()).hexdigest()) /
                    ("book-" + str(i) + ".epub") if nested and i >= 3
                    else root / ("book-" + str(i) + ".epub"))
            receipt(service, account, row, rights[row.record_id], i, path=path)
        session.download_direct = lambda *_a: pytest.fail("six exact local proofs require zero GET")
        result = execute(broker, account.identity_id, "startup")
        assert result.ok, result.facts
        assert (result.facts["skipped"], result.facts["attempted"],
                result.facts["downloaded"]) == (6, 0, 0)
    finally:
        service.close()


def test_three_unverified_legacy_purchases_recovered_without_copy_deletion(
    tmp_path, monkeypatch,
):
    service, account, session, broker, root, rows, rights = setup_six(tmp_path, monkeypatch)
    try:
        for i, row in enumerate(rows):
            receipt(service, account, row, rights[row.record_id], i,
                    path=root / ("book-" + str(i) + ".epub") if i < 3 else None)
        # Three physically present legacy files, but no exact origin/placement
        # proof for these purchases. They must not prohibit a permitted GET.
        legacy = []
        for i in range(3, 6):
            old = root / ("acsm-" + hashlib.sha256(str(i).encode()).hexdigest()) / "old.epub"
            epub(old)
            legacy.append(old)
        calls = []
        def deliver(item, target):
            calls.append(item.record_id)
            file = Path(target) / ("numilog-" + hashlib.sha256(
                item.record_id.encode()).hexdigest()[:32] + ".epub")
            epub(file)
            return file, ""
        session.download_direct = deliver
        first = execute(broker, account.identity_id, "startup")
        assert first.ok, first.facts
        assert first.facts["downloaded"] == 3
        assert first.facts["skipped"] == 3
        assert len(calls) == 3
        # Six different exact purchases may share one content-addressed
        # Artifact without losing any per-purchase right or Placement.
        purchases = service.storage.acquisitions(provider_id="numilog")
        assert len({x["artifact_id"] for x in purchases
                    if x["status"] == "complete" and x["artifact_id"]}) == 1
        assert all(p.is_file() for p in legacy)
        for row in rows[3:]:
            recovered = service.storage.acquisition_get(recovery_id(
                account.identity_id, account.credential_id,
                account.unique_id, row.record_id,
            ))
            assert recovered and recovered["status"] == "complete"
        # A second opening, even beyond the throttle, sees all six local
        # proofs and performs no redundant GET.
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.time",
            lambda: 2_000_000_000,
        )
        second = execute(broker, account.identity_id, "startup")
        assert second.facts["downloaded"] == 0
        assert len(calls) == 3
        assert all(p.is_file() for p in legacy)
    finally:
        service.close()


@pytest.mark.parametrize("history_status,expected", [
    ("complete", "recoverable"),
    ("attention_required", "recovery_receipt_pending"),
    ("retryable_error", "recovery_receipt_pending"),
])
def test_legacy_receipt_gate_is_scoped_and_fails_closed(tmp_path, monkeypatch, history_status, expected):
    service, account, _session, _broker, _root, rows, rights = setup_six(tmp_path, monkeypatch)
    try:
        receipt(service, account, rows[0], rights[rows[0].record_id], 0,
                status=history_status)
        assert recovery_gate(
            service.storage, identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id, item_id=rows[0].record_id,
        ) == expected
        assert recovery_gate(
            service.storage, identity_id="another-profile",
            credential_id=account.credential_id,
            account_id=account.unique_id, item_id=rows[0].record_id,
        ) == "recovery_right_unproven"
    finally:
        service.close()


def test_failed_purchase_does_not_starve_other_five(tmp_path, monkeypatch):
    service, account, session, broker, root, rows, rights = setup_six(tmp_path, monkeypatch)
    try:
        for i, row in enumerate(rows):
            receipt(service, account, row, rights[row.record_id], i)
        calls = []
        def deliver(item, target):
            calls.append(item.record_id)
            if item.record_id == rows[0].record_id:
                raise RuntimeError("provider failed for first purchase")
            path = Path(target) / ("numilog-" + hashlib.sha256(
                item.record_id.encode()).hexdigest()[:32] + ".epub")
            epub(path)
            return path, ""
        session.download_direct = deliver
        result = execute(broker, account.identity_id, "startup")
        assert result.facts["attempted"] == 6
        assert result.facts["downloaded"] == 5
        assert result.facts["failed"] == 1
        assert result.facts["status"] == "partial"
        assert len(calls) == 6
    finally:
        service.close()


def test_recovery_checkpoint_survives_restart_and_resumes_without_get(
    tmp_path, monkeypatch,
):
    """A crash after provider delivery must not replay a signed ACSM."""
    from cifereca.core.numilog_recovery import recovery_request
    from tests.test_sync_state_contract import _service

    service, account, session, broker, root, rows, rights = setup_six(tmp_path, monkeypatch)
    checkpoint_id = None
    try:
        row = rows[0]
        receipt(service, account, row, rights[row.record_id], 0)
        def deliver(item, target):
            path = Path(target) / ("numilog-" + hashlib.sha256(
                item.record_id.encode()).hexdigest()[:32] + ".epub")
            epub(path)
            return path, ""
        session.download_direct = deliver
        # Simulate an interruption AFTER the committed delivery checkpoint,
        # BEFORE index_delivered's independent catalog transaction.
        with recovery_request():
            acquired = service.acquire_direct_item(
                row, _admission=lambda: row,
            )
        checkpoint_id = acquired.metadata["numilog_checkpoint_id"]
        pending = service.storage.acquisition_get(checkpoint_id)
        assert pending["status"] == "attention_required"
        assert pending["metadata"]["phase"] == "index_pending"
    finally:
        service.close()

    restored = _service(tmp_path)
    try:
        resumed = restored.resume_numilog_materialization(checkpoint_id)
        assert resumed.indexed["status"] == "indexed"
        complete = restored.storage.acquisition_get(checkpoint_id)
        assert complete["status"] == "complete"
        assert complete["artifact_id"] == resumed.artifact_id
        # No connected Numilog session/provider is required to resume indexing.
        assert not any(a.provider_id == "numilog" for a in restored.accounts)
    finally:
        restored.close()


def test_foreign_placement_blocks_recovery_but_identical_sha_does_not(
    tmp_path, monkeypatch,
):
    service, account, _session, _broker, root, rows, rights = setup_six(tmp_path, monkeypatch)
    try:
        item = rows[0]
        receipt(service, account, item, rights[item.record_id], 0)
        foreign = service.library_identity_provider_dir("alex", "numilog") / "same.epub"
        epub(foreign)
        # A byte-identical file in another profile is an Artifact, NOT a
        # right for the current account. A different source item is harmless.
        indexed = service.catalog_index_path(
            foreign, item_id="foreign-item",
            identity_id="alex", provider_id="numilog",
        )
        assert indexed["status"] == "indexed"
        assert recovery_gate(
            service.storage, identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id, item_id=item.record_id,
        ) == "recoverable"
        # Conversely a foreign placement declaring THIS scoped source item
        # is a conflict, even if its file bytes have the same SHA.
        claimed = foreign.parent / "foreign-claim.epub"
        epub(claimed)
        service.storage.placement_put({
            "path": str(claimed), "identity_id": "alex",
            "provider_id": "numilog", "source_item_id": item.record_id,
            "artifact_id": indexed["artifact_id"], "state": "present",
        })
        assert recovery_gate(
            service.storage, identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id, item_id=item.record_id,
        ) == "recovery_foreign_placement"
    finally:
        service.close()


def test_authenticated_downloadable_without_cached_right_is_idempotent(
    tmp_path, monkeypatch,
):
    """Latest #3171: optional rights cannot veto a proven delivery offer."""
    from dataclasses import replace
    from cifereca.core import numilog_materialization_startup as startup

    service, account, session, _calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        row = replace(_row(account.credential_id, 91), owned=False,
                      downloadable=True)
        session.library = lambda: [row]
        requests = []
        def deliver(item, target):
            requests.append(item.record_id)
            file = Path(target) / "offered.epub"
            epub(file)
            return file, ""
        session.download_direct = deliver
        result = execute(broker, account.identity_id, "startup")
        assert result.ok, result.facts
        assert result.facts["downloaded"] == 1
        assert requests == [row.record_id]
        assert service.storage.entitlements(provider_id="numilog") == []
        saved = service.storage.acquisitions(provider_id="numilog")
        assert len(saved) == 1 and saved[0]["status"] == "complete"
        assert saved[0]["entitlement_id"] is None
        monkeypatch.setattr(startup, "time", lambda: 2_000_000_000)
        second = execute(broker, account.identity_id, "startup")
        assert second.ok, second.facts
        assert second.facts["skipped"] == 1
        assert second.facts["attempted"] == second.facts["downloaded"] == 0
        assert requests == [row.record_id]
    finally:
        service.close()


def test_untracked_legacy_files_never_veto_safe_isolated_get(
    tmp_path, monkeypatch,
):
    service, account, session, broker, root, rows, _rights = setup_six(tmp_path, monkeypatch)
    try:
        # An unindexed EPUB in an arbitrary historic subdirectory is NOT
        # a proof of edition, and it is NOT a reason to stop all downloads.
        orphan = root / "acsm-old" / "unlinked.epub"
        epub(orphan)
        initial = orphan.read_bytes()
        session.library = lambda: [rows[0]]
        requests = []
        def deliver(item, target):
            requests.append(Path(target))
            path = Path(target) / "delivered.epub"
            epub(path)
            return path, ""
        session.download_direct = deliver
        result = execute(broker, account.identity_id, "startup")
        assert result.ok, result.facts
        assert result.facts["downloaded"] == 1
        assert len(requests) == 1
        assert requests[0] != root
        assert requests[0].parent == root
        assert orphan.read_bytes() == initial
    finally:
        service.close()


def test_optional_rights_mirror_failure_does_not_veto_download(
    tmp_path, monkeypatch,
):
    service, account, session, _calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        row = _row(account.credential_id, 22)
        session.library = lambda: [row]
        # The positive provider delivery survives a broken metadata mirror.
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            lambda *_args, **_kwargs: 0,
        )
        requests = []
        def deliver(item, target):
            requests.append(item.record_id)
            path = Path(target) / "offered.epub"
            epub(path)
            return path, ""
        session.download_direct = deliver
        first = execute(broker, account.identity_id, "startup")
        assert first.ok, first.facts
        assert first.facts["downloaded"] == 1 and len(requests) == 1
        assert service.storage.entitlements(provider_id="numilog") == []
    finally:
        service.close()


def test_unknown_or_negative_download_offer_is_individually_blocked(
    tmp_path, monkeypatch,
):
    from dataclasses import replace
    service, account, session, _calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        rows = [replace(_row(account.credential_id, 1), owned=None,
                        downloadable=None),
                replace(_row(account.credential_id, 2), owned=False,
                        downloadable=False)]
        session.library = lambda: rows
        session.download_direct = lambda *_a: pytest.fail(
            "Unknown/negative provider capability must not GET"
        )
        result = execute(broker, account.identity_id, "startup")
        assert not result.ok
        assert result.facts["attempted"] == 0
        assert result.facts["blocked"] == 2
        assert result.facts["downloaded"] == 0
    finally:
        service.close()
