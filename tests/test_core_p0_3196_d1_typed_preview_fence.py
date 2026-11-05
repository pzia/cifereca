"""P0 #3196 D1: a typed Kobo edition cannot inherit local-presence claims
from an old v0.7 backup row or become a legacy download candidate.

Real Core service, disposable SQLite and synthetic EPUBs; provider GET/ADEPT
is not used and preview never modifies persisted provenance.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from tests.test_kobo_backup_preview import _backup
from tests.test_kobo_backup_fault_isolation import _service, _write_epub


def _typed(service, *, item="book-a", credential="vault-1",
           owner="default", borrower="buyer", state="active"):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("typed:source", owner, "kobo", credential, borrower,
             "purchases", item, "purchase", "purchase", state, 1, 1),
        )


def _persistent(service):
    with service.storage._lock, service.storage._connect() as db:
        return tuple(
            tuple(db.execute("SELECT * FROM " + table + " ORDER BY 1"))
            for table in ("backup_items", "source_references", "origin_links",
                          "acquisition_ledger", "entitlements", "artifacts",
                          "placements", "catalog_entries")
        )


@pytest.mark.parametrize("fresh,verify", [
    (False, False), (False, True), (True, False), (True, True),
])
@pytest.mark.parametrize("owner,borrower,state", [
    ("default", "buyer", "active"),
    ("default", "other-borrower", "unknown"),
    ("another-profile", "other-borrower", "inactive"),
])
def test_typed_source_never_gains_local_presence_from_backup_row(
    tmp_path, monkeypatch, fresh, verify, owner, borrower, state,
):
    service, session = _service(
        tmp_path, [{"product_id": "book-a", "revision_id": "r1",
                    "title": "Synthetic", "format": "EPUB"}],
    )
    try:
        file = service._kobo_backup_target_dir("default") / "saved.epub"
        _write_epub(file)
        raw = file.read_bytes()
        _backup(service, "book-a", status="complete", local_path=file,
                sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
        _typed(service, owner=owner, borrower=borrower, state=state)
        before = _persistent(service)
        monkeypatch.setattr(
            session, "resolve_acquisition",
            lambda *_a, **_kw: pytest.fail("no legacy descriptor"),
        )
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_a, **_kw: pytest.fail("no legacy GET"),
        )
        if not fresh:
            monkeypatch.setattr(
                session, "backup_manifest",
                lambda: pytest.fail("offline preview cannot fetch manifest"),
            )
        result = service.kobo_backup_preview(
            "vault-1", refresh_manifest=fresh, verify_local_files=verify,
        )
        assert result["counts"]["attention_required"] == 1
        assert result["counts"]["already_local_linked"] == 0
        assert result["counts"]["already_local_verified"] == 0
        assert result["counts"]["potentially_missing"] == 0
        assert result["counts"]["candidate_unverified"] == 0
        assert result["blocking_items"] == 1
        assert result["acquisition_authorized"] is False
        assert _persistent(service) == before
        assert file.read_bytes() == raw
        public = json.dumps(result)
        for raw_id in ("vault-1", "book-a", "saved.epub", borrower):
            assert raw_id not in public
    finally:
        service.close()


@pytest.mark.parametrize("other", [
    {"item": "different-edition"},
    {"credential": "another-credential"},
])
def test_unrelated_source_preserves_legacy_preview_compatibility(tmp_path, other):
    service, session = _service(tmp_path, [])
    try:
        file = service._kobo_backup_target_dir("default") / "legacy.epub"
        _write_epub(file)
        _backup(service, "book-a", status="complete", local_path=file)
        _typed(service, **other)
        before = _persistent(service)
        result = service.kobo_backup_preview("vault-1")
        assert result["counts"]["already_local_linked"] == 1
        assert result["counts"]["attention_required"] == 0
        assert _persistent(service) == before
    finally:
        service.close()


def test_mixed_preview_uses_core_for_typed_and_legacy_only_for_untyped(tmp_path):
    service, session = _service(tmp_path, [])
    try:
        for item in ("book-a", "book-b"):
            file = service._kobo_backup_target_dir("default") / f"{item}.epub"
            _write_epub(file)
            _backup(service, item, status="complete", local_path=file)
        _typed(service)
        result = service.kobo_backup_preview("vault-1")
        assert result["counts"]["attention_required"] == 1
        assert result["counts"]["already_local_linked"] == 1
        assert result["blocking_items"] == 1
        assert result["acquisition_authorized"] is False
    finally:
        service.close()


def test_typed_archived_remains_archived_not_present(tmp_path):
    service, session = _service(tmp_path, [
        {"product_id": "book-a", "revision_id": "r1",
         "archived": True, "format": "EPUB"},
    ])
    try:
        _typed(service)
        result = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert result["counts"]["skipped_archived"] == 1
        assert result["counts"]["already_local_linked"] == 0
        assert result["counts"]["potentially_missing"] == 0
        assert result["acquisition_authorized"] is False
    finally:
        service.close()
