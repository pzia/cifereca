"""P0 #2131: interrupted Numilog startup admission has a typed, zero-attempt receipt.

The cancellation happens before a durable claim and any provider/ADEPT I/O.
The existing diagnostic feed is reused and stays strictly redacted.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.numilog_materialization_startup import execute
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import Cancelled
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize


SENSITIVE = "Bearer SECRET_2131 account=private@example.invalid https://private.invalid/?token=SECRET_2131"


def _control(error):
    return NS(check_cancelled=Mock(side_effect=error))


def _completed(service):
    return [
        row["detail"] for row in service.diagnostics.snapshot()
        if row["category"] == "NUMILOG"
        and row["operation"] == "startup_materialization"
        and row["detail"].get("phase") == "completed"
    ]


def test_preclaim_cancel_emits_interrupted_phase_and_never_claims(tmp_path, monkeypatch):
    service, account, session, downloads, broker, sid = _setup(tmp_path)
    try:
        service.diagnostics = Diagnostics(level="normal")
        _authorize(broker, sid)
        session.library = Mock(side_effect=AssertionError("never request private library"))
        claim = Mock(side_effect=AssertionError("never claim startup receipt"))
        monkeypatch.setattr(
            service.storage, "numilog_materialization_claim_startup", claim,
        )
        control = _control(Cancelled(SENSITIVE))
        with pytest.raises(Cancelled):
            execute(broker, account.identity_id, "startup", control=control)
        control.check_cancelled.assert_called_once_with()
        claim.assert_not_called()
        session.library.assert_not_called()
        assert downloads == []
        details = _completed(service)
        assert len(details) == 1
        terminal = details[0]
        assert terminal["status"] == "blocked"
        assert terminal["code"] == "interrupted"
        assert terminal["cause_phase"] == "admission"
        assert terminal["attempted"] == 0
        assert terminal["downloaded"] == 0
        assert terminal["skipped"] == 0
        assert terminal["failed"] == 0
        assert service.storage.numilog_materialization_run_status(sid)["status"] == "never"
        snapshot = broker.numilog_materialization_run_status(sid)
        assert snapshot["diagnostic"] == terminal
        assert SENSITIVE not in json.dumps(snapshot)
        assert "SECRET_2131" not in service.diagnostics.text("test")
    finally:
        service.close()


def test_preclaim_unexpected_control_error_is_failed_not_silent(tmp_path, monkeypatch):
    service, account, session, downloads, broker, sid = _setup(tmp_path)
    try:
        service.diagnostics = Diagnostics(level="normal")
        _authorize(broker, sid)
        session.library = Mock(side_effect=AssertionError("never fetch catalogue"))
        claim = Mock(side_effect=AssertionError("never take ownership"))
        monkeypatch.setattr(service.storage, "numilog_materialization_claim_startup", claim)
        result = execute(
            broker, account.identity_id, "startup", control=_control(RuntimeError(SENSITIVE)),
        )
        assert result.ok is False
        assert result.facts["status"] == "failed"
        assert result.facts["sources_claimed"] == 0
        assert result.facts["attempted"] == 0
        assert result.facts["failed"] == 1
        assert claim.call_count == 0
        session.library.assert_not_called()
        assert downloads == []
        [terminal] = _completed(service)
        assert terminal["code"] == "admission_failed"
        assert terminal["cause_phase"] == "admission"
        assert terminal["status"] == "failed"
        assert terminal["attempted"] == 0 and terminal["failed"] == 1
        assert "SECRET_2131" not in json.dumps(result.facts)
        assert "SECRET_2131" not in service.diagnostics.text("test")
    finally:
        service.close()
