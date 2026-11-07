"""#3196 W2: a Kobo 0.7 manifest cannot outlive its issuing borrower/session.

Only synthetic provider manifests and SQLite. A rebind cannot authorize a
legacy backup row, canonical right or content GET.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests.test_kobo_backup_reconcile_prune import _service, _manifest_row


@pytest.mark.parametrize("change", [
    "borrower",
    "same_scope_replaced",
    "session_detached",
    "disconnect",
    "epoch_rebound",
    "connection_owner_rebound",
])
def test_manifest_rebind_never_acquires_under_later_legacy_account(
    tmp_path, monkeypatch, change,
):
    service = _service(tmp_path, manifest=[_manifest_row()])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    original = tuple(session.accounts)
    calls = []

    def switch_during_manifest():
        calls.append("rights-only-manifest")
        if change == "borrower":
            session._accounts = [replace(original[0], unique_id="other-borrower")]
        elif change == "same_scope_replaced":
            session._accounts = [replace(original[0])]
        elif change == "session_detached":
            service._sessions[service._session_key("kobo", "vault-1")] = object()
        elif change == "disconnect":
            session.connected = False
        elif change == "epoch_rebound":
            session._epoch = 42
        else:
            service.storage.provider_connection_put({
                "identity_id": "different-owner",
                "provider_id": "kobo",
                "credential_id": "vault-1",
                "label": "Rebound",
            })
        return [_manifest_row()]

    monkeypatch.setattr(session, "backup_manifest", switch_during_manifest)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail(
            "stale provider manifest must not start a content GET"
        ),
    )
    try:
        report = service.sync_kobo_backup_account("vault-1")
        assert calls == ["rights-only-manifest"]
        assert report["status"] == "failed"
        assert report["errors"] == 1
        assert report["attempted"] == report["downloaded"] == 0
        assert report["manifests_fetched"] == 0
        assert service.storage.backup_list("kobo", "vault-1") == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()


def test_stable_source_can_still_refresh_legacy_manifest_without_content_get(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    calls = []
    monkeypatch.setattr(session, "backup_manifest", lambda: calls.append("manifest") or [])
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("empty manifest may not acquire"),
    )
    try:
        report = service.sync_kobo_backup_account("vault-1")
        assert calls == ["manifest"]
        assert report["status"] == "complete"
        assert report["errors"] == report["downloaded"] == 0
        assert report["manifests_fetched"] == 1
    finally:
        service.close()


def test_source_rebind_before_http_does_not_fetch_manifest(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._accounts = [replace(session.accounts[0], credential_id="wrong")]
    calls = []
    monkeypatch.setattr(
        session, "backup_manifest", lambda: calls.append("manifest") or [],
    )
    try:
        report = service.sync_kobo_backup_account("vault-1")
        assert calls == []
        assert report["status"] == "failed"
        assert report["attempted"] == report["downloaded"] == 0
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()
