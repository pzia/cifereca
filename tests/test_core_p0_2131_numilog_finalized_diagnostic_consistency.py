"""P0 #2131: SQLite is the only authority for a Numilog finalized backup.

Pure Core schema / GUI text projections, with no GTK, HTTP, ACSM, SQL writes
or provider account; the original input mappings must remain unchanged.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from cifereca.core.numilog_startup_diagnostics import public_fields
from cifereca.gui.numilog_materialization_status import describe_run
from tests.test_numilog_startup_diagnostics import valid


NOW = 2_000_000_000
PRIVATE = "https://private.invalid/ebook.acsm?auth=NEVER_SHOW"


def _receipt(status="complete", *, attempted=2, downloaded=1, failed=0):
    return {
        "status": status, "started_at": NOW, "next_eligible_at": NOW + 3600,
        "attempted": attempted, "downloaded": downloaded,
        "skipped": 0, "failed": failed,
    }


def _diag(status="complete", *, attempted=2, downloaded=1, failed=0):
    return {
        **valid(), "status": status,
        "phase": "completed",
        "cause_phase": "completed" if status == "complete" else "manifest_fetch",
        "code": "complete" if status == "complete" else "manifest_failed",
        "attempted": attempted, "downloaded": downloaded,
        "skipped": 0, "failed": failed,
    }


@pytest.mark.parametrize("bad", [
    _diag("complete", failed=1),
    _diag("complete", attempted=0, downloaded=1),
    _diag("not_due", attempted=1, downloaded=0),
    _diag("not_due", attempted=0, downloaded=0, failed=1),
    _diag("failed", attempted=0, downloaded=0, failed=3),
])
def test_auxiliary_schema_refuses_impossible_success_or_count(bad):
    before = deepcopy(bad)
    assert public_fields(bad) == {}
    assert bad == before


@pytest.mark.parametrize(("durable", "diagnostic"), [
    (_receipt("complete"), _diag("failed", attempted=2, downloaded=1, failed=1)),
    (_receipt("complete"), _diag("complete", attempted=2, downloaded=0)),
    (_receipt("failed", attempted=2, downloaded=0, failed=1),
     _diag("failed", attempted=2, downloaded=0, failed=2)),
    (_receipt("blocked", attempted=0, downloaded=0, failed=0),
     _diag("failed", attempted=0, downloaded=0, failed=1)),
])
def test_finalized_sqlite_receipt_cannot_be_rewritten_by_conflicting_auxiliary(
    durable, diagnostic,
):
    row = {**durable, "diagnostic": diagnostic, "ignored_secret": PRIVATE}
    before = deepcopy(row)
    actual = describe_run("on_start", row, now=NOW)
    assert "Phase :" not in actual
    assert "cause :" not in actual
    assert "NEVER_SHOW" not in actual
    if durable["status"] == "complete":
        assert "Dernier essai terminé" in actual
        assert "1 téléchargé(s)" in actual
        assert "0 échec(s)" in actual
    elif durable["status"] == "failed":
        assert "Dernier essai en échec" in actual
        assert "1 échec(s)" in actual
    else:
        assert "Dernier essai bloqué" in actual
    assert row == before


def test_matching_terminal_auxiliary_keeps_diagnostic_phase_and_task_ref():
    row = {**_receipt(), "diagnostic": _diag()}
    text = describe_run("on_start", row, now=NOW)
    assert "Dernier essai terminé" in text
    assert "Phase : completed ; cause : complete" in text
    assert "tâche : abcdef012345" in text


def test_not_due_is_a_valid_fresh_observation_of_old_completed_receipt():
    row = {**_receipt(), "diagnostic": _diag(
        "not_due", attempted=0, downloaded=0, failed=0,
    )}
    # The worker emits the real throttling phase, not a new SQLite success.
    row["diagnostic"].update(cause_phase="durable_claim", code="not_due")
    text = describe_run("on_start", row, now=NOW)
    assert "Aucun nouvel essai" in text
    assert "0 tenté(s)" in text
    assert "Phase : durable_claim ; cause : not_due" in text
    assert row["attempted"] == 2 and row["downloaded"] == 1


@pytest.mark.parametrize(("durable", "attempted", "downloaded", "failed"), [
    ("failed", 1, 0, 1),
    ("partial", 2, 1, 1),
    ("blocked", 0, 0, 0),
    ("running", 0, 0, 0),
])
def test_not_due_does_not_mask_a_prior_unsuccessful_receipt(
    durable, attempted, downloaded, failed,
):
    """Screenshot #2024: no new claim must not turn last failure into 0 errors."""
    row = {
        **_receipt(
            durable, attempted=attempted, downloaded=downloaded, failed=failed,
        ),
        "diagnostic": _diag("not_due", attempted=0, downloaded=0, failed=0),
        "ignored_secret": PRIVATE,
    }
    row["diagnostic"].update(cause_phase="durable_claim", code="not_due")
    before = deepcopy(row)
    label = describe_run("on_start", row, now=NOW)
    assert "Aucun nouvel essai" not in label
    assert "Phase : durable_claim ; cause : not_due" not in label
    assert "Prochain essai : après la prochaine ouverture" in label
    if durable == "failed":
        assert "Dernier essai en échec" in label
        assert "1 tenté(s)" in label and "1 échec(s)" in label
    elif durable == "partial":
        assert "Dernier essai partiel" in label
        assert "1 téléchargé(s)" in label and "1 échec(s)" in label
    elif durable == "blocked":
        assert "Dernier essai bloqué" in label
    else:
        assert "Sauvegarde en cours ou interrompue" in label
    assert "NEVER_SHOW" not in label
    assert row == before
