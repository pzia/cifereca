"""P0 #1143: coordinator/ADEPT exceptions cannot enter persisted sync diagnostics."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from cifereca.models import Account
from tests.test_sync_state_contract import _service


PRIVATE = "https://private.invalid/LOAN/loan.acsm?auth=PRIVATE_COORDINATOR_TOKEN"


def _single_account(service):
    service.accounts = [Account(
        display_name="Paris", unique_id="reader", provider_id="paris",
        credential_id="private-credential", identity_id="default",
    )]


def test_identity_level_failure_has_closed_code_and_preserves_other_identity(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _single_account(service)
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda *_a, **_kw: [])
        original = service._persist_active_loans
        def fail_default(identity_id, loans):
            if identity_id == "default":
                raise RuntimeError(PRIVATE)
            return original(identity_id, loans)
        monkeypatch.setattr(service, "_persist_active_loans", fail_default)
        summary = service.opening_sync()
        first = next(x for x in summary["identities"]
                     if x["identity_id"] == "default")
        second = next(x for x in summary["identities"]
                      if x["identity_id"] == "alex")
        assert first["status"] == "failed"
        assert first["error"] == "Synchronisation impossible"
        assert first["error_code"] == "opening_sync_failed"
        assert second["status"] == "success"
        saved = service.storage.latest_sync_run("default")
        assert saved["detail"]["error_code"] == "opening_sync_failed"
        assert PRIVATE not in json.dumps(summary) + json.dumps(saved)
    finally:
        service.close()


def test_adept_profile_failure_is_code_only_and_does_not_abort_opening(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        monkeypatch.setattr(service, "_kobo_adapter",
                            lambda: NS(available=lambda: True))
        def fail_profiles():
            raise RuntimeError(PRIVATE)
        monkeypatch.setattr(service, "kobo_profiles", fail_profiles)
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda *_a, **_kw: [])
        summary = service.opening_sync()
        assert all(x["status"] == "success" for x in summary["identities"])
        rows = [x for x in service.diagnostics.snapshot()
                if x["operation"] == "profile list for dashboard"]
        assert len(rows) == 1
        assert rows[0]["detail"] == {"code": "adept_profiles_unavailable"}
        assert PRIVATE not in service.diagnostics.text("test") + json.dumps(summary)
    finally:
        service.close()
