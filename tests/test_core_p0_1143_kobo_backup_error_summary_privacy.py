"""P0 #1143: Kobo backup error classifiers and progress contain no private faults.

All provider sessions are fixture-only; no Kobo API call, Adobe runtime,
checkout, remote download or private credential is used.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.kobo_backup import _kobo_backup_error_info
from cifereca.kobo.errors import KoboAdeptError
from cifereca.kobo.vault import KoboVaultError
from tests.test_core_p0_1143_kobo_backup_error_boundary import _setup, ITEM


PRIVATE = "https://private.invalid/LOAN/reader.acsm?auth=TOKEN_KOBO_BACKUP"
CODE_SECRET = "TOKEN_KOBO_BACKUP"


class HostileException(RuntimeError):
    def __str__(self):
        raise AssertionError("Cannot stringify a provider exception")

    def __repr__(self):
        raise AssertionError("Cannot repr a provider exception")


HostileException.__name__ = CODE_SECRET


@pytest.mark.parametrize(("error", "code", "phase", "http_status", "fatal"), [
    (HostileException(PRIVATE), "backup_item_failed", "integration", None, False),
    (RuntimeError(PRIVATE), "backup_item_failed", "integration", None, False),
    (KoboVaultError(PRIVATE, code=CODE_SECRET, status_code=403),
     "provider_http_error", "vault", 403, True),
    (KoboVaultError(PRIVATE, code="missing_delegation_scope", status_code=403),
     "missing_delegation_scope", "vault", 403, False),
    (KoboVaultError(PRIVATE, code="temporary", status_code=503),
     "temporary", "vault", 503, False),
    (KoboAdeptError(PRIVATE, code=CODE_SECRET),
     "adept_error", "adept", None, False),
    (KoboAdeptError(PRIVATE, code="result_unparseable_artifact_present"),
     "result_unparseable_artifact_present", "adept", None, False),
    (RuntimeError("Kobo-Vault : bearer token invalide " + PRIVATE),
     "invalid_token", "vault", None, True),
    (RuntimeError("Kobo-Vault : scope insuffisant " + PRIVATE),
     "insufficient_scope", "vault", None, True),
])
def test_error_info_preserves_closed_classification_without_any_error_text(
    error, code, phase, http_status, fatal,
):
    info = _kobo_backup_error_info(error)
    assert info == {
        "phase": phase, "code": code,
        "status_code": http_status,
        "message": "Sauvegarde Kobo impossible",
        "fatal": fatal,
    }
    assert CODE_SECRET not in json.dumps(info)
    assert "private.invalid" not in json.dumps(info)


def test_item_failure_summary_and_progress_do_not_copy_private_error_payload(
    tmp_path, monkeypatch,
):
    service, session = _setup(tmp_path)
    def refuse(*_args, **_kwargs):
        raise KoboVaultError(PRIVATE, code=CODE_SECRET, status_code=403)
    monkeypatch.setattr(session, "resolve_acquisition", refuse)
    progress = []
    try:
        result = service.sync_kobo_backup(progress=progress.append)
        assert result["errors"] == 1
        assert result["aborted"] is True
        assert result["abort_reason"]["code"] == "provider_http_error"
        assert result["abort_reason"]["fatal"] is True
        assert result["error_summary"][0]["message"] == "Sauvegarde Kobo impossible"
        assert result["error_examples"][0]["message"] == "Sauvegarde Kobo impossible"
        assert result["error_messages"] == ["Sauvegarde Kobo impossible"]
        exported_errors = json.dumps({
            "error_summary": result["error_summary"],
            "abort_reason": result["abort_reason"],
            "error_messages": result["error_messages"],
        })
        assert CODE_SECRET not in exported_errors
        error_events = [event for event in progress if event.get("event") == "item_error"]
        assert len(error_events) == 1
        assert error_events[0]["message"] == "Sauvegarde Kobo impossible"
        assert error_events[0]["code"] == "provider_http_error"
        assert CODE_SECRET not in error_events[0]["message"] + error_events[0]["code"]
        persisted = service.storage.backup_get("kobo", "vault-1", ITEM)
        assert persisted["error"] == "Sauvegarde Kobo impossible"
    finally:
        service.close()
