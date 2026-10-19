"""P0 #2131: an optional Numilog trace cannot attest SQLite completion.

Pure status projection tests: no network, files, GTK or ADEPT mutations.
"""
from __future__ import annotations

import pytest

from cifereca.gui.numilog_materialization_status import describe_run
from tests.test_numilog_startup_diagnostics import valid


def _receipt(status: str, diagnostic_status: str) -> dict:
    raw = {
        "status": status,
        "started_at": 2_000_000_000,
        "next_eligible_at": 2_000_003_600,
        "attempted": 0, "downloaded": 0, "skipped": 0,
        "failed": 1 if status == "failed" else 0,
        "diagnostic": {
            **valid(), "status": diagnostic_status,
            "cause_phase": "completed",
            "code": "complete" if diagnostic_status == "complete" else "manifest_failed",
            "failed": 0 if diagnostic_status == "complete" else 1,
        },
    }
    return raw


@pytest.mark.parametrize("durable", ["running", "failed", "blocked", "unknown", "never"])
def test_diagnostic_complete_never_promotes_unfinished_durable_receipt(durable):
    data = _receipt(durable, "complete")
    original = dict(data, diagnostic=dict(data["diagnostic"]))
    text = describe_run("on_start", data, now=2_000_000_001)
    assert "Dernier essai terminé" not in text
    assert "Phase : completed" not in text
    assert "cause : complete" not in text
    if durable in {"unknown", "never"}:
        assert "indisponible" in text.lower() or "Aucun essai" in text
    else:
        assert "Dernier essai" in text or "Sauvegarde en cours" in text
    assert data == original  # projection cannot modify durable evidence


def test_confirmed_sqlite_receipt_may_show_matching_complete_diagnostic():
    data = _receipt("complete", "complete")
    text = describe_run("on_start", data, now=2_000_000_001)
    assert "Dernier essai terminé" in text
    assert "Phase : completed ; cause : complete" in text


def test_running_sqlite_receipt_still_displays_actual_failed_finalization_diagnostic():
    data = _receipt("running", "failed")
    data["diagnostic"].update(
        cause_phase="receipt_finalization", code="receipt_finalize_failed",
    )
    text = describe_run("on_start", data, now=2_000_000_001)
    assert "Dernier essai en échec" in text
    assert "Phase : receipt_finalization" in text
    assert "cause : receipt_finalize_failed" in text
    assert "1 échec(s)" in text
