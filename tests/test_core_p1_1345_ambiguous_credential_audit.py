"""Core P1 #1345: fail closed on ambiguous Kobo credential ownership.

A local SHA coincidence cannot be attributed to another identity merely
because two persisted connection rows reused a credential. The guard runs
before any ledger read, disk scan, download or provider query.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.kobo_local_audit import kobo_local_reconciliation_audit
from tests.test_kobo_backup_fault_isolation import _service


def _kobo_row(service):
    return dict(next(row for row in service.storage.provider_connections()
                     if row["provider_id"] == "kobo"))


@pytest.mark.parametrize("reversed_order", [False, True])
@pytest.mark.parametrize("scoped", [False, True])
def test_cross_identity_credential_conflict_is_rejected_before_any_other_read(
    tmp_path, monkeypatch, reversed_order, scoped,
):
    service, _ = _service(tmp_path, [])
    try:
        original = _kobo_row(service)
        other = {**original, "identity_id": "PRIVATE_OTHER_PROFILE"}
        rows = [original, other]
        if reversed_order:
            rows.reverse()
        monkeypatch.setattr(service.storage, "provider_connections", lambda: rows)

        def forbidden(*args, **kwargs):
            pytest.fail("ambiguous Kobo scope must not read ledger, files or rights")

        for name in ("backup_list", "catalog_entries", "entitlements"):
            monkeypatch.setattr(service.storage, name, forbidden)
        monkeypatch.setattr(service, "identity_by_id", forbidden)
        monkeypatch.setattr(service, "sync_kobo_backup", forbidden)
        with pytest.raises(ValueError) as exc:
            kobo_local_reconciliation_audit(
                service,
                credential_id=original["credential_id"] if scoped else None,
            )
        assert str(exc.value) == "ambiguous_kobo_audit_scope"
        for private in (original["credential_id"], "PRIVATE_OTHER_PROFILE"):
            assert private not in str(exc.value)
    finally:
        service.close()


@pytest.mark.parametrize("bad_field", ["credential_id", "identity_id"])
def test_empty_credential_or_identity_cannot_use_a_fallback_root(
    tmp_path, monkeypatch, bad_field,
):
    service, _ = _service(tmp_path, [])
    try:
        wrong = _kobo_row(service)
        wrong[bad_field] = ""
        monkeypatch.setattr(service.storage, "provider_connections", lambda: [wrong])
        monkeypatch.setattr(
            service.storage, "backup_list",
            lambda *_args: pytest.fail("invalid root must not start audit"),
        )
        with pytest.raises(ValueError, match="^invalid_kobo_audit_scope$"):
            kobo_local_reconciliation_audit(service)
    finally:
        service.close()


def test_exact_duplicate_connection_is_counted_once_not_double_hashed(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    try:
        reference = kobo_local_reconciliation_audit(service)
        connection = _kobo_row(service)
        monkeypatch.setattr(
            service.storage, "provider_connections",
            lambda: [dict(connection), dict(connection), dict(connection)],
        )
        deduplicated = kobo_local_reconciliation_audit(service)
        assert deduplicated == reference
        assert len(deduplicated["accounts"]) == 1
        assert deduplicated["accounts"][0]["account"] == "account-1"
        assert connection["credential_id"] not in json.dumps(deduplicated)
        assert connection["identity_id"] not in json.dumps(deduplicated)
    finally:
        service.close()


def test_other_provider_with_same_credential_never_creates_kobo_conflict(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    try:
        reference = kobo_local_reconciliation_audit(service)
        original = _kobo_row(service)
        other = {
            **original, "provider_id": "paris",
            "identity_id": "PRIVATE_OTHER_PROFILE",
        }
        monkeypatch.setattr(
            service.storage, "provider_connections", lambda: [original, other],
        )
        report = kobo_local_reconciliation_audit(service)
        assert report == reference
        assert "PRIVATE_OTHER_PROFILE" not in json.dumps(report)
    finally:
        service.close()


def test_empty_connection_list_remains_a_valid_empty_audit(
    tmp_path, monkeypatch,
):
    service, _ = _service(tmp_path, [])
    try:
        monkeypatch.setattr(service.storage, "provider_connections", lambda: [])
        report = kobo_local_reconciliation_audit(service)
        assert report["accounts"] == []
        assert report["counts"]["backup_rows"] == 0
        assert report["offline"] is True and report["read_only"] is True
        assert report["mutations_authorized"] is False
        assert report["downloads_authorized"] is False
    finally:
        service.close()


@pytest.mark.parametrize("stored_identity", [None, "", "PRIVATE_OTHER_PROFILE"])
def test_backup_legacy_row_owner_mismatch_blocks_audit_before_catalog_or_files(
    tmp_path, monkeypatch, stored_identity,
):
    """The connection owner never substitutes for a legacy row's own owner."""
    service, _ = _service(tmp_path, [])
    try:
        connection = _kobo_row(service)
        credential = connection["credential_id"]
        monkeypatch.setattr(
            service.storage, "backup_list",
            lambda provider: [{
                "provider_id": "kobo", "credential_id": credential,
                "identity_id": stored_identity, "item_id": "private-book",
                "local_path": str(tmp_path / "OTHER_PROFILE" / "book.epub"),
            }] if provider == "kobo" else [],
        )

        def forbidden(*args, **kwargs):
            pytest.fail("foreign legacy row cannot trigger catalogue, rights or file reads")

        for name in ("catalog_entries", "entitlements"):
            monkeypatch.setattr(service.storage, name, forbidden)
        monkeypatch.setattr(service, "identity_by_id", forbidden)
        with pytest.raises(ValueError, match="^ambiguous_kobo_audit_scope$"):
            kobo_local_reconciliation_audit(service, credential_id=credential)
    finally:
        service.close()


@pytest.mark.parametrize("right_identity", [None, "", "PRIVATE_OTHER_PROFILE"])
def test_canonical_right_owner_mismatch_never_enters_kobo_file_audit(
    tmp_path, monkeypatch, right_identity,
):
    service, _ = _service(tmp_path, [])
    try:
        connection = _kobo_row(service)
        credential = connection["credential_id"]
        monkeypatch.setattr(service.storage, "backup_list", lambda *_: [])
        monkeypatch.setattr(service.storage, "catalog_entries", lambda: [])
        monkeypatch.setattr(
            service.storage, "entitlements",
            lambda **kwargs: [{
                "provider_id": "kobo", "credential_id": credential,
                "identity_id": right_identity, "item_id": "private-book",
                "state": "active",
            }],
        )

        def forbidden(*args, **kwargs):
            pytest.fail("foreign right cannot inspect a Kobo identity's files")

        monkeypatch.setattr(service, "identity_by_id", forbidden)
        with pytest.raises(ValueError, match="^ambiguous_kobo_audit_scope$"):
            kobo_local_reconciliation_audit(service, credential_id=credential)
    finally:
        service.close()
