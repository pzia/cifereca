"""P0 #4858 W2/D2: an exact canonical CAS policy revokes legacy Kobo I/O.

SQLite, connected synthetic legacy Kobo sessions and fake provider transport;
no real Kobo, ADEPT or user files. Legacy remains enabled only when CAS unset.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from cifereca.core import kobo_backup
from cifereca.core.source_sync_consent import policy_key, scope_for_account
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from cifereca.providers import ProviderConfigurationError
from cifereca.storage import Storage
from tests.test_kobo_backup_guard import _service as _guard_service


def _policy(storage, scope, enabled):
    raw = (
        "malformed-canonical-consent"
        if enabled is None else json.dumps({
            "schema": "cifereca.source-sync-consent/1",
            "scope": [scope.identity_id, scope.provider_id,
                      scope.credential_id, scope.account_id],
            "enabled": enabled, "revision": "a" * 32,
        })
    )
    storage.metadata_set(policy_key(scope), raw)


def _snapshot(service):
    storage = service.storage
    with storage._lock, storage._connect() as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "backup_items", "entitlements", "acquisition_ledger",
                "artifacts", "placements", "catalog_entries",
                "source_references", "origin_links",
            )
        }


@pytest.mark.parametrize("enabled", (True, False, None), ids=("on", "off", "invalid"))
@pytest.mark.parametrize("scoped", (False, True), ids=("global", "one-account"))
def test_configured_cas_blocks_w2_before_local_or_provider_io(
    tmp_path, monkeypatch, enabled, scoped,
):
    service = _guard_service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    scope = ProviderRightsScope("alice", "kobo", "vault-1", session.accounts[0].unique_id)
    _policy(service.storage, scope, enabled)
    before = _snapshot(service)

    def forbidden(*_a, **_kw):
        pytest.fail("CAS policy must veto legacy scan, manifest, ADEPT and GET")

    monkeypatch.setattr(service, "catalog_scan", forbidden)
    monkeypatch.setattr(service, "kobo_adept_state", forbidden)
    monkeypatch.setattr(session, "backup_manifest", forbidden)
    monkeypatch.setattr(service, "_execute_kobo_acquisition", forbidden)
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_legacy_canonical_consent_configured"):
            kobo_backup._sync_kobo_backup_locked(
                service, logger=logging.getLogger("test"),
                credential_id="vault-1" if scoped else None,
            )
        assert _snapshot(service) == before
    finally:
        service.close()


def test_unset_cas_preserves_legacy_manifest_compatibility(tmp_path, monkeypatch):
    service = _guard_service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    seen = []
    monkeypatch.setattr(session, "backup_manifest",
                        lambda: seen.append("manifest") or [])
    monkeypatch.setattr(service, "catalog_scan", lambda **_kw: None)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("empty manifest cannot acquire"),
    )
    try:
        report = kobo_backup._sync_kobo_backup_locked(
            service, logger=logging.getLogger("test"), credential_id="vault-1",
        )
        assert seen == ["manifest"]
        assert report["attempted"] == 0
    finally:
        service.close()


def test_in_flight_cas_revocation_stops_next_legacy_get(
    tmp_path, monkeypatch,
):
    service = _guard_service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    scope = ProviderRightsScope("alice", "kobo", "vault-1", session.accounts[0].unique_id)
    monkeypatch.setattr(service, "catalog_scan", lambda **_kw: None)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("rebound consent must prevent GET"),
    )
    progress_events = []

    def progress(event):
        progress_events.append(event["event"])
        if event["event"] == "scan":
            _policy(service.storage, scope, False)

    try:
        report = kobo_backup._sync_kobo_backup_locked(
            service, logger=logging.getLogger("test"),
            credential_id="vault-1", progress=progress,
        )
        assert "scan" in progress_events
        assert report["attempted"] == report["downloaded"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
    finally:
        service.close()


@pytest.mark.parametrize("enabled", (True, False, None), ids=("on", "off", "invalid"))
def test_final_checkpoint_writer_denies_cas_even_without_session_callback(
    tmp_path, enabled,
):
    storage = Storage(tmp_path / "state.sqlite3")
    scope = ProviderRightsScope("alice", "kobo", "vault-1", "account")
    try:
        _policy(storage, scope, enabled)
        row = {
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "alice", "account_id": "account",
            "item_id": "book-1", "revision_id": "r1",
            "status": "complete", "sha256": "a" * 64, "size": 42,
        }
        with pytest.raises(kobo_backup._KoboLegacySessionChanged,
                           match="kobo_legacy_canonical_consent_configured"):
            kobo_backup._guarded_kobo_legacy_put(
                SimpleNamespace(storage=storage), row, None,
            )
        assert storage.backup_list("kobo") == []
        assert storage.entitlements(provider_id="kobo") == []
        assert storage.placements() == []
    finally:
        storage.close()


@pytest.mark.parametrize("mode", (True, False, None), ids=("on", "off", "invalid"))
def test_legacy_manual_preview_rejects_configured_cas_without_manifest(
    case, monkeypatch, mode,
):
    """Existing D2 fixture owns three identities; only exact target is gated."""
    account = case.target.accounts[0]
    scope = scope_for_account(account)
    _policy(case.service.storage, scope, mode)
    monkeypatch.setattr(
        case.target, "backup_manifest",
        lambda: pytest.fail("no provider inventory after configured source policy"),
    )
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok
    assert result.facts["status"] == "canonical_source_policy_configured"
    assert "confirmation_token" not in result.facts
    assert not case.downloaded
    assert not any(case.calls.values())


def test_legacy_confirmation_token_revoked_after_canonical_cas_opt_in(case):
    token = case.broker.prepare_local_materialization(
        case.source,
    ).facts["confirmation_token"]
    _policy(case.service.storage, scope_for_account(case.target.accounts[0]), True)
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok
    assert result.facts["status"] == "canonical_source_policy_configured"
    assert not case.downloaded


# Reuse a real broker/SQLite fixture with synthetic transport.
from tests.test_source_materialization_confirmation import case  # noqa: E402,F401


@pytest.mark.parametrize("enabled", (True, False, None), ids=("on", "off", "invalid"))
def test_independent_w2_recovery_refuses_preconfigured_cas(tmp_path, enabled):
    """The offline recovery helper is callable without the W2 batch gate."""
    from tests.test_core_p0_3196_w2_recovery_live_session_scope import (
        _recover_only, _interrupted,
    )
    from tests.test_kobo_backup_reconcile_prune import _service

    service = _service(tmp_path, manifest=[])
    try:
        service.storage.backup_put(_interrupted("vault-1", "interrupted"))
        before = service.storage.backup_get("kobo", "vault-1", "interrupted")
        scope = ProviderRightsScope("default", "kobo", "vault-1", "account")
        _policy(service.storage, scope, enabled)
        summary = _recover_only(service)
        assert summary["recovered_interrupted"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "interrupted") == before
    finally:
        service.close()


def test_independent_w2_recovery_refuses_cas_change_mid_scan(tmp_path, monkeypatch):
    from tests.test_core_p0_3196_w2_recovery_live_session_scope import (
        _recover_only, _interrupted,
    )
    from tests.test_kobo_backup_reconcile_prune import _service

    service = _service(tmp_path, manifest=[])
    try:
        service.storage.backup_put(_interrupted("vault-1", "interrupted"))
        before = service.storage.backup_get("kobo", "vault-1", "interrupted")
        scope = ProviderRightsScope("default", "kobo", "vault-1", "account")
        original = service._kobo_record_candidate_paths
        called = []

        def change_policy_during_scan(*args, **kwargs):
            called.append(True)
            _policy(service.storage, scope, False)
            return original(*args, **kwargs)

        monkeypatch.setattr(
            service, "_kobo_record_candidate_paths", change_policy_during_scan,
        )
        with pytest.raises(kobo_backup._KoboLegacySessionChanged,
                           match="kobo_legacy_session_changed"):
            _recover_only(service)
        assert called
        assert service.storage.backup_get("kobo", "vault-1", "interrupted") == before
    finally:
        service.close()
