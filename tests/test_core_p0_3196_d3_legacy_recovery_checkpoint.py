"""P0 #3196 D3: legacy direct Kobo download cannot replay unresolved receipts.

Real Storage and LibraryService under a synthetic acquisition lock. No
supplier HTTP, ADEPT or physical deletions. Current typed rights remain
governed by the stronger #3196 SourceReference cutover.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cifereca import services
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _manifest_row, _service


@pytest.mark.parametrize("status,path,digest,size,attention", [
    ("pending", "/unmounted/historical-book.epub", None, None, None),
    ("pending", None, "a" * 64, None, None),
    ("error", None, None, 106, None),
    ("acquiring", None, None, None, None),
    ("complete", None, None, None, None),
    ("reconcile_required", None, None, None, None),
    ("reconcile_conflict", None, None, None, None),
    ("pending_acquisition", None, None, None, "interrupted_acquisition_review"),
    ("attention_required", None, None, None, "missing_delegation_scope"),
    ("attention_required", None, None, None, "archived_book"),
    ("skipped_archived", None, None, None, None),
])
def test_direct_legacy_cannot_reget_when_historical_recovery_unresolved(
    tmp_path, monkeypatch, status, path, digest, size, attention,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    item = session.normalize_item(_manifest_row())
    entries = []

    @contextmanager
    def lock(_path):
        entries.append("acquisition-lock")
        yield

    try:
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "alice", "account_id": "account",
            "item_id": "book-1", "revision_id": "old-revision",
            "title": "Historical", "entitlement_type": "owned",
            "status": status, "local_path": path, "sha256": digest,
            "size": size, "attention_code": attention,
        })
        before = (
            service.storage.backup_list(),
            service.storage.acquisitions(provider_id="kobo"),
            service.storage.entitlements(provider_id="kobo"),
            service.storage.placements(),
        )
        monkeypatch.setattr(services, "InterProcessLock", lock)
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args: pytest.fail("no second legacy Kobo GET"),
        )
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_direct_reconciliation_required",
        ):
            service._acquire_direct_item_impl(item)
        assert entries == ["acquisition-lock"]
        assert (
            service.storage.backup_list(),
            service.storage.acquisitions(provider_id="kobo"),
            service.storage.entitlements(provider_id="kobo"),
            service.storage.placements(),
        ) == before
    finally:
        service.close()


def test_never_started_legacy_item_preserves_explicit_direct_capability(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    item = service._sessions[
        service._session_key("kobo", "vault-1")
    ].normalize_item(_manifest_row())

    @contextmanager
    def lock(_path):
        yield

    try:
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "alice", "account_id": "account",
            "item_id": "book-1", "revision_id": "old-revision",
            "title": "Historical", "entitlement_type": "owned",
            "status": "pending", "local_path": None,
        })
        calls = []
        monkeypatch.setattr(services, "InterProcessLock", lock)
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda received, _target: (
                calls.append(received.record_id) or
                SimpleNamespace(path=tmp_path / "synthetic.epub", metadata={})
            ),
        )
        service._acquire_direct_item_impl(item)
        assert calls == ["book-1"]
    finally:
        service.close()
