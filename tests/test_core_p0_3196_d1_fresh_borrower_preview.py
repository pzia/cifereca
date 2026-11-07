"""#3196 D1/W2: a fresh Kobo manifest cannot adopt another borrower's 0.7 path.

Synthetic SQLite/files and provider rights manifest only; no content GET/ADEPT.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_kobo_backup_fault_isolation import _service, _write_epub
from tests.test_kobo_backup_preview import _forbid_mutation_and_acquisition


def _rig(tmp_path, *, owner, borrower, archived=False):
    manifest = [{
        "product_id": "book", "revision_id": "rev-1",
        "title": "Synthetic", "format": "EPUB",
        "owned": True, "downloadable": True,
        **({"archived": True} if archived else {}),
    }]
    service, session = _service(tmp_path, manifest)
    path = service._kobo_backup_target_dir("default") / "book.epub"
    _write_epub(path)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": owner, "account_id": borrower,
        "item_id": "book", "revision_id": "rev-1",
        "title": "Synthetic", "format": "EPUB", "status": "complete",
        "local_path": str(path),
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    return service, session, path


@pytest.mark.parametrize("owner,borrower", [
    ("default", "former-borrower"),
    ("other-profile", "account"),
    (None, "account"),
    ("default", None),
])
def test_fresh_private_preview_refuses_foreign_or_unbound_legacy_file(
    tmp_path, monkeypatch, owner, borrower,
):
    service, session, path = _rig(tmp_path, owner=owner, borrower=borrower)
    before = service.storage.backup_get("kobo", "vault-1", "book")
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    try:
        reasons = {}
        result = service.kobo_backup_preview(
            "vault-1", refresh_manifest=True, verify_local_files=True,
            _item_reasons=reasons,
        )
        assert reasons == {"book": "attention_required"}
        assert result["counts"]["attention_required"] == 1
        assert result["counts"]["already_local_verified"] == 0
        assert result["counts"]["already_local_linked"] == 0
        assert result["counts"]["potentially_missing"] == 0
        assert result["acquisition_authorized"] is False
        assert service.storage.backup_get("kobo", "vault-1", "book") == before
        assert path.is_file()
    finally:
        service.close()


def test_same_exact_borrower_retains_private_legacy_repair_preflight(
    tmp_path, monkeypatch,
):
    service, session, _ = _rig(tmp_path, owner="default", borrower="account")
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    try:
        reasons = {}
        result = service.kobo_backup_preview(
            "vault-1", refresh_manifest=True, verify_local_files=True,
            _item_reasons=reasons,
        )
        assert reasons == {"book": "already_local_verified"}
        assert result["counts"]["already_local_verified"] == 1
        assert result["acquisition_authorized"] is False
    finally:
        service.close()


def test_fresh_explicit_archive_flag_is_not_inferred_from_old_borrower(
    tmp_path, monkeypatch,
):
    service, session, _ = _rig(
        tmp_path, owner="default", borrower="former-borrower", archived=True,
    )
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    try:
        reasons = {}
        result = service.kobo_backup_preview(
            "vault-1", refresh_manifest=True, _item_reasons=reasons,
        )
        assert reasons == {"book": "skipped_archived"}
        assert result["counts"]["skipped_archived"] == 1
    finally:
        service.close()
