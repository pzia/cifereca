"""P0 #3196 D1: a typed Kobo edition cannot inherit local-presence claims
from an old v0.7 backup row or become a legacy download candidate.

Real Core service, disposable SQLite and synthetic EPUBs; provider GET/ADEPT
is not used and preview never modifies persisted provenance.
"""
from __future__ import annotations

import hashlib

import pytest

from cifereca.providers import ProviderConfigurationError

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


def _no_legacy_preview_authority(service, *, refresh=False, verify=False):
    """D1 can refuse or return canonical-B-only history, never a 0.7 claim."""
    try:
        result = service.kobo_backup_preview(
            "vault-1", refresh_manifest=refresh, verify_local_files=verify,
        )
    except ProviderConfigurationError as exc:
        assert str(exc) in {
            "canonical_kobo_preview_scope_unavailable",
            "canonical_kobo_preview_scope_changed",
            "canonical_kobo_preview_abi_unavailable",
        }
        return
    assert not refresh, "partial ABI cannot refresh provider A"
    assert result["acquisition_authorized"] is False
    assert result["canonical_source"] is True
    assert result["historical_canonical_only"] is True
    assert result["manifest_refreshed"] is False
    for key in ("already_local_linked", "already_local_verified", "potentially_missing"):
        assert result["counts"][key] == 0


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
            session, "backup_manifest",
            lambda: pytest.fail("retired D1 must not GET a 0.7 manifest"),
        )
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_a, **_kw: pytest.fail("no legacy GET"),
        )
        _no_legacy_preview_authority(service, refresh=fresh, verify=verify)
        assert _persistent(service) == before
        assert file.read_bytes() == raw
    finally:
        service.close()


@pytest.mark.parametrize("other", [
    {"item": "different-edition"},
    {"credential": "another-credential"},
])
def test_unrelated_source_cannot_promote_legacy_offline_local_claim(tmp_path, other):
    service, session = _service(tmp_path, [])
    try:
        file = service._kobo_backup_target_dir("default") / "legacy.epub"
        _write_epub(file)
        _backup(service, "book-a", status="complete", local_path=file)
        _typed(service, **other)
        before = _persistent(service)
        _no_legacy_preview_authority(service)
        assert _persistent(service) == before
        assert file.is_file()
    finally:
        service.close()


def test_mixed_typed_and_untyped_legacy_receipts_never_prove_b(tmp_path):
    service, session = _service(tmp_path, [])
    try:
        for item in ("book-a", "book-b"):
            file = service._kobo_backup_target_dir("default") / f"{item}.epub"
            _write_epub(file)
            _backup(service, item, status="complete", local_path=file)
        _typed(service)
        before = _persistent(service)
        _no_legacy_preview_authority(service)
        assert _persistent(service) == before
    finally:
        service.close()


def test_archived_typed_right_cannot_become_legacy_presence(tmp_path):
    service, session = _service(tmp_path, [
        {"product_id": "book-a", "revision_id": "r1",
         "archived": True, "format": "EPUB"},
    ])
    try:
        _typed(service)
        before = _persistent(service)
        _no_legacy_preview_authority(service, refresh=True)
        assert _persistent(service) == before
    finally:
        service.close()


@pytest.mark.parametrize("fresh", [False, True])
def test_historical_archive_flag_cannot_hide_typed_edition(tmp_path, fresh):
    service, session = _service(tmp_path, [
        {"product_id": "book-a", "revision_id": "r1",
         "title": "A", "format": "EPUB"},
    ])
    try:
        _backup(service, "book-a", status="skipped_archived")
        _typed(service)
        before = _persistent(service)
        _no_legacy_preview_authority(service, refresh=fresh)
        assert _persistent(service) == before
    finally:
        service.close()
