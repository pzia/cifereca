"""P0 #4858 D1/D2: a partial ABI preview cannot authorize foreign or 0.7 rights."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core import kobo_backup
from tests.test_kobo_backup_preview import (
    _backup, _forbid_mutation_and_acquisition,
)
from tests.test_kobo_backup_fault_isolation import _service, _write_epub


@pytest.mark.parametrize("claim", [
    {"account_id": "foreign"},
    {"account_id": None},
    {"credential_id": "different-vault"},
    {"provider_id": "another-provider"},
    {"loan_id": "loan-1"},
    {"expires_at": "2026-11-30"},
    {"entitlement_type": "loan"},
    {"entitlement_type": True},
    {"id": "other-edition"},
])
@pytest.mark.parametrize("private", [False, True])
def test_refreshed_legacy_preview_refuses_foreign_or_loan_claims(
    tmp_path: Path, monkeypatch, claim: dict, private: bool,
):
    item = {"product_id": "book-1", "revision_id": "r1", "title": "Book One",
            "format": "EPUB", "downloadable": True, "owned": True, **claim}
    service, session = _service(tmp_path, [item])
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    try:
        reasons = {} if private else None
        result = kobo_backup.kobo_backup_preview(
            service, "vault-1", refresh_manifest=True,
            verify_local_files=True, _item_reasons=reasons,
        )
        assert result["counts"]["invalid_manifest_items"] == 1
        assert result["counts"]["potentially_missing"] == 0
        assert result["counts"]["already_local_verified"] == 0
        assert result["counts"]["already_local_linked"] == 0
        assert result["manifest_valid"] is False
        assert result["acquisition_authorized"] is False
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
        if private:
            assert reasons == {}
    finally:
        service.close()


@pytest.mark.parametrize("verify", [False, True])
@pytest.mark.parametrize("status", ["ok", "complete"])
def test_private_confirmation_sees_old_complete_only_as_reconciliation(
    tmp_path: Path, monkeypatch, verify: bool, status: str,
):
    service, session = _service(tmp_path, [
        {"product_id": "book-1", "revision_id": "r1",
         "title": "Book One", "format": "EPUB", "downloadable": True},
    ])
    artifact = service._kobo_backup_target_dir("default") / "untrusted.epub"
    _write_epub(artifact)
    import hashlib
    _backup(service, "book-1", status=status, local_path=artifact,
            account_id="account", sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            size=artifact.stat().st_size)
    archive = service.storage.backup_get("kobo", "vault-1", "book-1")
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    monkeypatch.setattr(
        kobo_backup, "_observed_backup_artifact",
        lambda *_a, **_kw: pytest.fail("0.7 receipt cannot authorize file inspection"),
    )
    try:
        reasons = {}
        result = kobo_backup.kobo_backup_preview(
            service, "vault-1", refresh_manifest=True,
            verify_local_files=verify, _item_reasons=reasons,
        )
        assert reasons == {"book-1": "needs_reconciliation"}
        assert result["blocking_items"] == 1
        assert result["counts"]["potentially_missing"] == 0
        assert result["counts"]["already_local_verified"] == 0
        assert result["counts"]["already_local_linked"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == archive
    finally:
        service.close()
