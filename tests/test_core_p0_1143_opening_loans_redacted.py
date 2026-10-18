"""P0 #1143: opening loan sync never persists provider exception bodies.

Real LibraryService and SQLite; provider fetches are synthetic and no user
credentials, ADEPT actions or network calls are performed.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace as NS

from tests.test_core_p0_1346_opening_source_isolation import _accounts, _state
from tests.test_sync_state_contract import _service


PRIVATE = "https://private.invalid/LOAN/reader.acsm?auth=SECRET_OPENING_LOANS"


def test_opening_exception_is_safe_in_log_export_and_sqlite(tmp_path, monkeypatch, caplog):
    service = _service(tmp_path)
    try:
        paris, kobo, numilog = _accounts(service)
        calls = []
        def loans(account, *, force=False):
            calls.append(account.provider_id)
            if account is paris:
                raise RuntimeError(PRIVATE)
            return []
        monkeypatch.setattr(service, "_loans_for_account", loans)
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = service.opening_sync()

        default = next(row for row in result["identities"]
                       if row["identity_id"] == "default")
        assert default["status"] == "partial"
        persisted = service.storage.latest_sync_run("default")
        assert persisted["detail"]["provider_errors"] == [{"code": "loan_source_failed"}]
        assert _state(service, paris)["status"] == "failed"
        assert _state(service, kobo)["status"] == "success"
        assert _state(service, numilog)["status"] == "success"
        assert calls == ["paris", "kobo", "numilog"]
        visible = (json.dumps(result) + json.dumps(persisted)
                   + service.diagnostics.text("test") + caplog.text)
        assert "SECRET_OPENING_LOANS" not in visible
        assert "private.invalid" not in visible
        assert "Traceback (most recent call last)" not in caplog.text
        assert "TRACE loans sync error code=loan_source_failed" in caplog.text
    finally:
        service.close()


def test_partial_upstream_errors_are_counted_without_exporting_raw_error_fields(
    tmp_path, monkeypatch, caplog,
):
    service = _service(tmp_path)
    try:
        paris, kobo, numilog = _accounts(service)
        service._sessions[service._session_key("paris", paris.credential_id)] = NS(
            provider_id="paris", credential_id=paris.credential_id,
            connected=True,
            capabilities=NS(loans=True),
            last_loan_errors=[
                {"upstream": PRIVATE, "error": PRIVATE},
                {"upstream": PRIVATE, "error": PRIVATE},
            ],
        )
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda account, force=False: [])
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = service.opening_sync()
        default = next(row for row in result["identities"]
                       if row["identity_id"] == "default")
        assert default["status"] == "partial"
        assert _state(service, paris)["status"] == "partial"
        assert _state(service, kobo)["status"] == "success"
        assert service.storage.latest_sync_run("default")["detail"]["provider_errors"] == [
            {"code": "loan_partial_failure"}, {"code": "loan_partial_failure"},
        ]
        visible = json.dumps(result) + json.dumps(
            service.storage.latest_sync_run("default")) + caplog.text
        assert PRIVATE not in visible
        assert "loan_partial_failure count=2" in caplog.text
        assert all("paris-private" not in line for line in caplog.text.splitlines()
                   if "TRACE loans sync" in line)
    finally:
        service.close()
