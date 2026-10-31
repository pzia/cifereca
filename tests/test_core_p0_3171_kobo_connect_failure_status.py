"""#3171: Kobo Vault connect failures are not an opening-sync success.

All cases are offline: no credential reset, HTTP request or acquisition.
"""
from __future__ import annotations

import logging

from cifereca.core import provider_sessions, sync_coordination
from tests.test_core_p0_1143_provider_connect_privacy import _rig, SIGNED
from tests.test_kobo_sync_modes import _service, _neutralize_unrelated_sync_work


class UnsafeUpstreamError(RuntimeError):
    code = "invalid_token"

    def __str__(self):
        raise AssertionError("untrusted exception must not be rendered")


def test_closed_connection_cause_rejects_untrusted_prose_and_objects():
    assert provider_sessions._public_connection_cause(UnsafeUpstreamError(SIGNED)) == "invalid_token"
    for code in (SIGNED, "invalid_token:secret", None, 123, True, {}):
        exc = Exception(SIGNED)
        exc.code = code
        assert provider_sessions._public_connection_cause(exc) == "unclassified"


def test_provider_connect_logs_closed_upstream_cause_only(caplog):
    state, session, _plugin = _rig()
    session.connect.side_effect = UnsafeUpstreamError(SIGNED)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(
            state, logger=logging.getLogger("cifereca"),
        )
    assert result["connected"] == []
    assert result["errors"]
    assert "reason_code=invalid_token" in caplog.text
    assert SIGNED not in caplog.text
    assert "UnsafeUpstreamError" not in caplog.text


def test_unconnected_kobo_account_makes_opening_sync_failed(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        service.config.set("providers.accounts", [{
            "id": "vault", "provider": "kobo", "identity_id": "default",
            "enabled": True, "credentials": {"api_token": "not-used"},
            "options": {"base_url": "https://vault.invalid"},
        }])
        service._sessions.clear()
        service.accounts.clear()
        service._session_errors = {
            service._session_key("kobo", "vault"): "Connexion à la source indisponible",
        }
        result = service._opening_sync_locked(deep=False)
        own = next(row for row in result["identities"] if row["identity_id"] == "default")
        assert own["status"] == "failed"
        assert own["errors"] >= 1
        assert own["connection_failures"] == 1
        assert result["kobo_decision"]["status"] in {"disabled", "no_session"}
    finally:
        service.close()


def test_connection_failures_are_scoped_to_configured_identity(tmp_path):
    service = _service(tmp_path)
    try:
        service.config.set("providers.accounts", [{
            "id": "vault", "provider": "kobo", "identity_id": "another-identity",
            "enabled": True,
        }])
        service._session_errors = {
            service._session_key("kobo", "vault"): "Connexion à la source indisponible",
        }
        assert sync_coordination._opening_failed_configured_connections(service, "default") == 0
        assert sync_coordination._opening_failed_configured_connections(service, "another-identity") == 1
    finally:
        service.close()
