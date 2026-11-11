"""#3121: first-ACSM staging must report a closed verdict without secrets.

Real Core manual claim, synthetic Paris transport only.
"""
from __future__ import annotations

import logging

import pytest

from tests.test_paris_manual_borrow_dispatch import setup, attempt


@pytest.mark.parametrize("kind,verdict,reason", [
    ("acsm", "staged", "verified_initial_receipt"),
    ("no-acsm", "refused", "initial_candidate_unavailable"),
])
def test_confirmed_paris_borrow_reports_staging_verdict_without_token(
    tmp_path, caplog, kind, verdict, reason,
):
    service, session, account, item, state = setup(tmp_path, result=kind)
    try:
        with caplog.at_level(
            logging.INFO, logger="cifereca.core.manual_library_borrow",
        ):
            outcome = attempt(service, account, item)
        assert outcome["status"] == "confirmed"
        assert outcome["provider_verified"] is True
        assert state["mutations"] == 1
        notes = [
            row.message for row in caplog.records
            if row.name == "cifereca.core.manual_library_borrow"
            and "TRACE initial_loan_acsm" in row.message
        ]
        assert notes == [
            f"TRACE initial_loan_acsm outcome={verdict} reason_code={reason}"
        ]
        assert not any("https://" in msg or "loan-exact" in msg for msg in notes)
    finally:
        service.close()


def test_unexpected_staging_error_cannot_unconfirm_loan_or_log_exception(
    tmp_path, monkeypatch, caplog,
):
    service, session, account, item, state = setup(tmp_path)
    def broken(*_a, **_kw):
        raise RuntimeError("https://signed.invalid/secret.acsm?bearer=SENSITIVE")
    monkeypatch.setattr(
        "cifereca.core.manual_library_borrow._stage_confirmed_initial_loan_acsm",
        broken,
    )
    try:
        with caplog.at_level(
            logging.INFO, logger="cifereca.core.manual_library_borrow",
        ):
            outcome = attempt(service, account, item)
        assert outcome["status"] == "confirmed"
        assert state["mutations"] == 1
        notes = [
            row.message for row in caplog.records
            if row.name == "cifereca.core.manual_library_borrow"
        ]
        assert notes == [
            "TRACE initial_loan_acsm outcome=refused reason_code=initial_staging_error"
        ]
        assert "bearer" not in "".join(notes)
    finally:
        service.close()
