"""#3196 W2: a 0.7 manifest may not write or GET after session rebind.

Synthetic manifest, SQLite, and local-only acquisition stubs. No Kobo network
or real EPUB/DRM account.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_kobo_backup_reconcile_prune import _manifest_row, _service


@pytest.mark.parametrize("change", ("borrower", "disconnect", "owner", "disabled", "epoch"))
def test_progress_scan_rebind_aborts_before_any_legacy_item_write(
    tmp_path, monkeypatch, change,
):
    service = _service(tmp_path, manifest=[_manifest_row()])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    calls = []

    def change_scope(event):
        if event.get("event") != "scan":
            return
        if change == "borrower":
            session._accounts = [
                replace(session.accounts[0], unique_id="other-borrower")
            ]
        elif change == "disconnect":
            session.connected = False
        elif change == "owner":
            service.storage.provider_connection_put({
                "identity_id": "other", "provider_id": "kobo",
                "credential_id": "vault-1",
            })
        elif change == "disabled":
            service.storage.provider_connection_put({
                "identity_id": "default", "provider_id": "kobo",
                "credential_id": "vault-1", "enabled": False,
            })
        else:
            session._epoch = 123

    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("stale source must not GET"),
    )
    try:
        summary = service.sync_kobo_backup_account("vault-1", progress=change_scope)
        assert summary["status"] == "aborted"
        assert summary["aborted"] is True
        assert summary["attempted"] == summary["downloaded"] == 0
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()


def test_cover_metadata_rebind_cannot_refresh_existing_legacy_receipt(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[_manifest_row(title="Updated")])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    row = {
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "account_id": session.accounts[0].unique_id,
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Old", "status": "pending",
    }
    service.storage.backup_put(row)
    before = service.storage.backup_get("kobo", "vault-1", "book-1")

    def cover_and_rebind(_raw):
        session._accounts = [
            replace(session.accounts[0], unique_id="other-borrower")
        ]
        return "https://public.test/new-cover.jpg"

    monkeypatch.setattr(session, "cover_url", cover_and_rebind)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("rebind cannot GET"),
    )
    try:
        summary = service.sync_kobo_backup_account("vault-1")
        assert summary["status"] == "aborted"
        assert summary["attempted"] == summary["downloaded"] == 0
        assert summary["metadata_refreshed"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
    finally:
        service.close()


@pytest.mark.parametrize("phase", ("before_get", "after_get"))
def test_rebind_at_delivery_keeps_interrupted_receipt_without_false_complete(
    tmp_path, monkeypatch, phase,
):
    service = _service(tmp_path, manifest=[_manifest_row()])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    calls = []
    original_accounts = tuple(session.accounts)
    path = tmp_path / "synthetic-download.epub"

    def rebind():
        session._accounts = [
            replace(session.accounts[0], unique_id="other-borrower")
        ]

    def normalize_and_rebind(_raw, **_kwargs):
        # Normalization runs after the acquiring receipt and before GET.
        if phase == "before_get":
            rebind()
        return SimpleNamespace(
            record_id="book-1", source_provider="kobo",
            source_credential="vault-1", revision_id="rev-1",
            owned=True, downloadable=True,
        )

    def fake_delivery(*_args, **_kwargs):
        calls.append("GET")
        path.write_bytes(b"synthetic result of already-started provider GET")
        rebind()
        return SimpleNamespace(path=path, account_id=None, metadata={})

    # The minimal synthetic BackupSession has no provider-specific normalizer.
    # Supply one here so the test reaches the actual pre- and post-GET fence.
    monkeypatch.setattr(
        session, "normalize_item",
        normalize_and_rebind,
        raising=False,
    )
    monkeypatch.setattr(service, "_execute_kobo_acquisition", fake_delivery)
    monkeypatch.setattr(
        service, "_observed_backup_artifact",
        lambda _path: {"format": "EPUB", "size": 42, "sha256": "a" * 64},
    )
    try:
        summary = service.sync_kobo_backup_account("vault-1")
        assert summary["status"] == "aborted"
        assert summary["downloaded"] == 0
        assert calls == ([] if phase == "before_get" else ["GET"])
        receipt = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert receipt["status"] == "acquiring"
        assert receipt["sha256"] is None
        assert receipt["local_path"] is None
        assert path.exists() is (phase == "after_get")
        # Restore the exact upstream session; an interrupted 0.7 attempt
        # must not make a second content GET on the next manifest.
        session._accounts = list(original_accounts)
        repeat = service.sync_kobo_backup_account("vault-1")
        assert repeat["downloaded"] == repeat["attempted"] == 0
        assert calls == ([] if phase == "before_get" else ["GET"])
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] != "complete"
    finally:
        service.close()



def test_declared_presence_verification_cannot_replace_outer_session_guard(
    tmp_path, monkeypatch,
):
    """W2 epoch check survives the nested declared-presence callback."""
    from cifereca.core import library_declared_links, library_declared_presence

    service = _service(tmp_path, manifest=[_manifest_row()])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    monkeypatch.setattr(
        library_declared_links, "has_active_declared_link",
        lambda *_a, **_kw: True,
    )

    def verify_and_rebind(*_args, **_kwargs):
        session._epoch = 42
        return SimpleNamespace(status="unverified")

    monkeypatch.setattr(
        library_declared_presence, "verify_declared_presence", verify_and_rebind,
    )
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("stale declaration must not GET"),
    )
    try:
        report = service.sync_kobo_backup_account("vault-1")
        assert report["status"] == "aborted"
        assert report["attempted"] == report["downloaded"] == 0
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()
