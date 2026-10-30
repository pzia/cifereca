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
