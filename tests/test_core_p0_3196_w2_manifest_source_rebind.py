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


@pytest.mark.parametrize("mutation", (
    "secondary_before_manifest",
    "secondary_during_manifest",
    "primary_flag_changed_in_place",
))
def test_empty_kobo_borrower_is_only_valid_for_exact_primary_w2_session(
    tmp_path, monkeypatch, mutation,
):
    """The same empty borrower ID cannot grant a secondary a legacy GET."""
    service = _service(tmp_path, manifest=[_manifest_row()])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    primary = replace(session.accounts[0], unique_id="", is_primary=True)
    session._accounts = [primary]
    calls = []

    if mutation == "secondary_before_manifest":
        session._accounts = [replace(primary, is_primary=False)]

    def manifest():
        calls.append("manifest")
        if mutation == "secondary_during_manifest":
            session._accounts = [replace(primary, is_primary=False)]
        elif mutation == "primary_flag_changed_in_place":
            # Even when the Account object identity is unchanged, its
            # semantic primary flag is part of the pinned source scope.
            primary.is_primary = False
        return [_manifest_row()]

    monkeypatch.setattr(session, "backup_manifest", manifest)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail(
            "secondary borrower cannot acquire under primary empty ID"
        ),
    )
    try:
        report = service.sync_kobo_backup_account("vault-1")
        assert calls == ([] if mutation == "secondary_before_manifest" else ["manifest"])
        assert report["status"] == "failed"
        assert report["errors"] == 1
        assert report["attempted"] == report["downloaded"] == 0
        assert report["manifests_fetched"] == 0
        assert service.storage.backup_list("kobo", "vault-1") == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()


def test_empty_primary_kobo_borrower_still_refreshes_w2_manifest(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._accounts = [replace(session.accounts[0], unique_id="", is_primary=True)]
    calls = []
    monkeypatch.setattr(session, "backup_manifest", lambda: calls.append("manifest") or [])
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("empty manifest cannot GET"),
    )
    try:
        report = service.sync_kobo_backup_account("vault-1")
        assert calls == ["manifest"]
        assert report["status"] == "complete"
        assert report["errors"] == report["downloaded"] == 0
        assert report["manifests_fetched"] == 1
    finally:
        service.close()
