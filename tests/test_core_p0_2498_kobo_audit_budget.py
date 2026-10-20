"""P0 #2498: a 522+ Kobo library must not stop at 256 SHA probes.

All files are tiny fake byte payloads in the test sandbox. The audit stays
offline and read-only; no rights, file moves, deletion, repairs or GETs.
"""
from __future__ import annotations

import hashlib
import json

import pytest

import cifereca.core.kobo_local_audit as audit
from tests.test_core_1345_kobo_local_audit import _no_mutation
from tests.test_kobo_backup_fault_isolation import _service


def _fake_rows(root, count, *, size=80):
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(count):
        # Distinct local artifacts, no real EPUB/ACSM or account data.
        data = (f"FAKE_{i:06d}".encode() * (1 + size // 11))[:size]
        path = root / f"PRIVATE_{i:06d}.epub"
        path.write_bytes(data)
        rows.append({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "default", "item_id": f"PRIVATE_PRODUCT_{i}",
            "status": "complete", "local_path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
        })
    return rows


def _audit_fixture(service, monkeypatch, rows):
    monkeypatch.setattr(
        service.storage, "backup_list",
        lambda provider_id: list(rows) if provider_id == "kobo" else [],
    )
    # Rights are read-only persisted data for the test. Their absence must
    # never be inferred from the on-disk file names.
    monkeypatch.setattr(
        service.storage, "entitlements",
        lambda **kwargs: [{
            "credential_id": row["credential_id"],
            "identity_id": row["identity_id"],
            "item_id": row["item_id"], "state": "active",
        } for row in rows],
    )


def test_530_distinct_persisted_placements_are_verified_and_not_truncated(
    tmp_path, monkeypatch,
):
    service, session = _service(tmp_path, [])
    root = service._kobo_backup_target_dir("default")
    rows = _fake_rows(root, 530, size=30)
    _audit_fixture(service, monkeypatch, rows)
    _no_mutation(service, monkeypatch)
    monkeypatch.setattr(
        session, "backup_manifest",
        lambda: pytest.fail("audit must never request online Kobo manifest"),
    )
    before = [(row["local_path"], row["sha256"]) for row in rows]
    try:
        report = audit.kobo_local_reconciliation_audit(
            service, credential_id="vault-1",
        )
        assert report["truncated"] is False
        assert report["counts"]["backup_rows"] == 530
        assert report["counts"]["linked_files_verified"] == 530
        assert report["counts"]["unverified_placements"] == 0
        assert report["counts"]["active_entitlements_unmaterialized"] == 0
        assert report["counts"]["unindexed_regular_file_candidates"] == 0
        assert report["files_hashed_limit"] >= 530
        assert report["directory_entries_limit"] >= 530
        assert report["total_hash_bytes_read"] == sum(
            __import__("os").stat(path).st_size for path, _ in before
        )
        assert report["total_hash_bytes_read"] <= report["total_hash_bytes_limit"]
        assert report["read_only"] and report["offline"] and report["dry_run"]
        assert report["downloads_authorized"] is False
        assert report["mutations_authorized"] is False
        serialized = json.dumps(report)
        assert "PRIVATE_PRODUCT" not in serialized
        assert "PRIVATE_0000" not in serialized
        assert "vault-1" not in serialized and str(tmp_path) not in serialized
        for path, sha in before:
            assert __import__("os").path.isfile(path)
            assert hashlib.sha256(open(path, "rb").read()).hexdigest() == sha
    finally:
        service.close()


def test_cumulative_bytes_exhaustion_is_explicit_and_cannot_authorize_repair(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    rows = _fake_rows(service._kobo_backup_target_dir("default"), 3, size=32)
    _audit_fixture(service, monkeypatch, rows)
    monkeypatch.setattr(audit, "_MAX_TOTAL_HASH_BYTES", 40)
    try:
        report = audit.kobo_local_reconciliation_audit(service)
        assert report["truncated"] is True
        assert report["counts"]["backup_rows"] == 3
        assert report["counts"]["linked_files_verified"] == 1
        assert report["counts"]["unverified_placements"] == 2
        assert report["counts"]["active_entitlements_unmaterialized"] == 2
        assert report["total_hash_bytes_read"] == 32
        assert report["total_hash_bytes_limit"] == 40
        assert not report["mutations_authorized"]
        assert not report["downloads_authorized"]
    finally:
        service.close()


def test_remaining_hard_file_ceiling_cannot_silently_declare_complete(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    rows = _fake_rows(service._kobo_backup_target_dir("default"), 4, size=32)
    _audit_fixture(service, monkeypatch, rows)
    monkeypatch.setattr(audit, "_MAX_FILES", 2)
    try:
        report = audit.kobo_local_reconciliation_audit(service)
        assert report["truncated"] is True
        assert report["counts"]["linked_files_verified"] == 2
        assert report["counts"]["unverified_placements"] == 2
        assert report["files_hashed_limit"] == 2
        assert report["downloads_authorized"] is False
    finally:
        service.close()


def test_directory_scan_remains_bounded_independently_from_sha_budget(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    folder = service._kobo_backup_target_dir("default")
    _fake_rows(folder, 5, size=32)
    monkeypatch.setattr(audit, "_MAX_DIRECTORY_ENTRIES", 2)
    try:
        report = audit.kobo_local_reconciliation_audit(service)
        assert report["truncated"] is True
        assert report["counts"]["unindexed_regular_file_candidates"] <= 2
        assert report["counts"]["linked_files_verified"] == 0
        assert report["directory_entries_limit"] == 2
        assert report["downloads_authorized"] is False
    finally:
        service.close()
