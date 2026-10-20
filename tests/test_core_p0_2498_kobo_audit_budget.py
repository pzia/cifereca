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
        assert report["truncation_reasons"] == []
        assert report["hash_attempts_used"] == 530
        assert 530 <= report["directory_entries_inspected"] <= 4096
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
        assert report["truncation_reasons"] == ["hash_byte_budget"]
        assert report["hash_attempts_used"] == 3
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
        assert report["truncation_reasons"] == ["hash_attempt_budget"]
        assert report["hash_attempts_used"] == 2
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
        assert report["truncation_reasons"] == ["directory_entry_budget"]
        assert report["directory_entries_inspected"] == 2
        assert report["counts"]["unindexed_regular_file_candidates"] <= 2
        assert report["counts"]["linked_files_verified"] == 0
        assert report["directory_entries_limit"] == 2
        assert report["downloads_authorized"] is False
    finally:
        service.close()


def test_no_follow_unsupported_platform_fails_closed_without_reading(
    tmp_path, monkeypatch,
):
    """No SHA may be read if the platform cannot reject symlink traversal."""
    folder = tmp_path / "private"
    folder.mkdir()
    path = folder / "private.epub"
    path.write_bytes(b"synthetic-epub")
    monkeypatch.delattr(audit.os, "O_NOFOLLOW", raising=False)
    # A valid file is deliberately not read: returning a digest would be
    # unsafe because a symlinked parent could escape the owning identity.
    assert audit._read_hash_within(str(path), (folder,)) is None


def test_no_follow_missing_preserves_partial_evidence_and_no_download(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    rows = _fake_rows(service._kobo_backup_target_dir("default"), 1)
    _audit_fixture(service, monkeypatch, rows)
    monkeypatch.delattr(audit.os, "O_NOFOLLOW", raising=False)
    try:
        report = audit.kobo_local_reconciliation_audit(service)
        assert report["counts"]["backup_rows"] == 1
        assert report["counts"]["linked_files_verified"] == 0
        assert report["counts"]["unverified_placements"] >= 1
        assert report["counts"]["active_entitlements_unmaterialized"] == 1
        assert report["downloads_authorized"] is False
        assert report["mutations_authorized"] is False
        assert report["read_only"] and report["offline"]
    finally:
        service.close()


def test_large_persisted_snapshot_marks_record_budget_not_missing_files(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    # The 4097th row is intentionally not examined, with no file reads.
    # No path, title or source ID may escape the report.
    rows = [
        {"credential_id": "vault-1", "identity_id": "default",
         "item_id": f"PRIVATE_PRODUCT_{index}", "local_path": None}
        for index in range(4097)
    ]
    _audit_fixture(service, monkeypatch, rows)
    try:
        report = audit.kobo_local_reconciliation_audit(
            service, credential_id="vault-1",
        )
        assert report["truncated"] is True
        assert report["truncation_reasons"] == [
            "active_entitlements_budget", "backup_records_budget",
        ]
        assert report["counts"]["backup_rows"] == 4096
        assert report["hash_attempts_used"] == 0
        assert report["downloads_authorized"] is False
        assert "PRIVATE_PRODUCT_" not in json.dumps(report)
    finally:
        service.close()


def test_directory_scan_unavailable_is_not_reported_as_budget_exhaustion(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    directory = service._kobo_backup_target_dir("default")
    directory.mkdir(parents=True, exist_ok=True)

    def inaccessible(_fd):
        raise PermissionError("PRIVATE_LIBRARY_PATH")

    monkeypatch.setattr(audit.os, "scandir", inaccessible)
    try:
        report = audit.kobo_local_reconciliation_audit(service)
        assert report["truncated"] is True
        assert report["truncation_reasons"] == ["directory_scan_unavailable"]
        assert report["directory_entries_inspected"] == 0
        assert report["hash_attempts_used"] == 0
        assert report["downloads_authorized"] is False
        assert "PRIVATE_LIBRARY_PATH" not in json.dumps(report)
    finally:
        service.close()


def test_more_than_eight_local_product_paths_has_specific_bounded_reason(
    tmp_path,
):
    service, _ = _service(tmp_path, [])
    try:
        from tests.test_core_1345_kobo_local_audit import _backup
        _backup(service, "PRIVATE-product-path-limit")
        for index in range(9):
            service.storage.catalog_entry_put({
                "catalog_entry_id": f"fixture-placement-{index}",
                "title": "PRIVATE_LOCAL",
                "provider_id": "kobo", "identity_id": "default",
                "item_id": "PRIVATE-product-path-limit",
                "local_state": "local",
                "metadata": {"path": str(tmp_path / f"PRIVATE_{index}.epub")},
            })
        report = audit.kobo_local_reconciliation_audit(service)
        assert report["truncated"] is True
        assert "product_path_budget" in report["truncation_reasons"]
        assert report["hash_attempts_used"] <= report["files_hashed_limit"]
        assert report["downloads_authorized"] is False
        assert "PRIVATE-product-path-limit" not in json.dumps(report)
        assert str(tmp_path) not in json.dumps(report)
    finally:
        service.close()


@pytest.mark.parametrize("origin,reason", [
    ("backup", "backup_records_budget"),
    ("catalogue", "catalogue_rows_budget"),
    ("rights", "active_entitlements_budget"),
])
def test_persisted_row_limit_identifies_exact_scoped_source_without_secrets(
    tmp_path, monkeypatch, origin, reason,
):
    service, session = _service(tmp_path, [])
    monkeypatch.setattr(audit, "_MAX_RECORDS", 2)
    rows = [
        {"credential_id": "vault-1", "identity_id": "default",
         "item_id": f"PRIVATE_ITEM_{index}", "local_path": None}
        for index in range(3)
    ]
    monkeypatch.setattr(
        service.storage, "backup_list",
        lambda provider: rows if origin == "backup" and provider == "kobo" else [],
    )
    monkeypatch.setattr(
        service.storage, "catalog_entries",
        lambda: [
            {"provider_id": "kobo", "identity_id": "default",
             "item_id": f"PRIVATE_ITEM_{index}", "local_state": "remote",
             "metadata": {"private": "PRIVATE_KEY"}}
            for index in range(3)
        ] if origin == "catalogue" else [],
    )
    monkeypatch.setattr(
        service.storage, "entitlements",
        lambda **kwargs: [
            {"credential_id": "vault-1", "identity_id": "default",
             "item_id": f"PRIVATE_ITEM_{index}", "state": "active"}
            for index in range(3)
        ] if origin == "rights" else [],
    )
    monkeypatch.setattr(
        session, "backup_manifest",
        lambda: pytest.fail("audit must stay offline"),
    )
    try:
        report = audit.kobo_local_reconciliation_audit(
            service, credential_id="vault-1",
        )
        assert report["truncated"] is True
        assert report["truncation_reasons"] == [reason]
        assert report["hash_attempts_used"] == 0
        assert report["offline"] and report["read_only"] and report["dry_run"]
        assert report["downloads_authorized"] is False
        assert report["mutations_authorized"] is False
        assert "PRIVATE" not in json.dumps(report)
        assert "vault-1" not in json.dumps(report)
    finally:
        service.close()


def test_foreign_persisted_rows_do_not_consume_selected_record_budget(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    monkeypatch.setattr(audit, "_MAX_RECORDS", 2)
    monkeypatch.setattr(
        service.storage, "backup_list",
        lambda provider: [
            {"credential_id": "OTHER_PRIVATE", "identity_id": "OTHER_PRIVATE",
             "item_id": f"PRIVATE_{index}", "local_path": None}
            for index in range(5)
        ] if provider == "kobo" else [],
    )
    monkeypatch.setattr(
        service.storage, "catalog_entries",
        lambda: [
            {"provider_id": "kobo", "identity_id": "OTHER_PRIVATE",
             "item_id": f"PRIVATE_{index}", "local_state": "local"}
            for index in range(5)
        ],
    )
    monkeypatch.setattr(
        service.storage, "entitlements",
        lambda **kwargs: [
            {"credential_id": "OTHER_PRIVATE", "identity_id": "OTHER_PRIVATE",
             "item_id": f"PRIVATE_{index}", "state": "active"}
            for index in range(5)
        ],
    )
    try:
        report = audit.kobo_local_reconciliation_audit(
            service, credential_id="vault-1",
        )
        assert report["truncated"] is False
        assert report["truncation_reasons"] == []
        assert report["counts"]["backup_rows"] == 0
        assert "OTHER_PRIVATE" not in json.dumps(report)
    finally:
        service.close()
