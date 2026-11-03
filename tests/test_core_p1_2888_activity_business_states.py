"""#2888: activity is not a red error when Core says pending/partial/busy."""
from __future__ import annotations

import logging

import pytest

from cifereca.diagnostic_task_context import (
    diagnostic_task_business_result, diagnostic_task_reference, diagnostic_task_scope,
)
from cifereca.gui.activity_feedback import ActivityFeedback
from cifereca.gui.task_activity_adapter import task_started, task_finished
from cifereca.plugin_api import SyncResult
from tests.test_core_ui_1132_task_activity_adapter import make_window
from tests.test_core_p0_981_redacted_task_traces import _runner


@pytest.mark.parametrize(("state", "label", "icon"), [
    ("pending", "En attente", "dialog-information-symbolic"),
    ("partial", "Partielle", "dialog-warning-symbolic"),
    ("busy", "Déjà en cours", "dialog-information-symbolic"),
    ("blocked", "Bloquée", "dialog-warning-symbolic"),
    ("cancelled", "Annulée", "dialog-information-symbolic"),
])
def test_existing_feedback_and_gtk_adapter_keep_nonfatal_states_distinct(
    state, label, icon,
):
    window, _actions = make_window()
    task_started(window, 12, 1, "Synchroniser", 20, "source-sync")
    assert task_finished(
        window, 12, 0, "Synchroniser", False, "Résultat métier fermé", state,
    ) is False
    entry = window.activity_feedback.get(12)
    assert entry.state == state
    assert entry.display_text.startswith(f"{label} · Synchronisation d’une source")
    assert window.activity_feedback.latest_unacknowledged_error() is None
    assert window.busy_label.label == entry.display_text
    assert ("icon", icon) in window.activity_status_icon.calls
    assert ("add", "activity-error") not in window.activity_status_icon.calls
    assert ("add", "activity-success") not in window.activity_status_icon.calls


def test_failed_or_unknown_result_still_creates_real_error_and_success_wins():
    window, _actions = make_window()
    task_started(window, 1, 1, "Prêt", 20, "borrow")
    task_finished(window, 1, 0, "Prêt", False, "Échec attesté", "failed")
    assert window.activity_feedback.get(1).state == "error"
    assert window.activity_feedback.latest_unacknowledged_error().task_id == 1

    feedback = ActivityFeedback()
    assert feedback.finished(
        2, "Privé", "source-sync", success=False,
        error="http://private.invalid/?token=SECRET",
        business_status="PRIVATE_PROVIDER_STATE",
    ).state == "error"
    assert "private.invalid" not in feedback.rows()[0].display_text
    assert feedback.finished(
        3, "Confirmé", "borrow", success=True,
        business_status="pending",
    ).state == "success"


@pytest.mark.parametrize(("kind", "receipt", "expected"), [
    ("source-sync", SyncResult(ok=False, facts={
        "status": "partial", "canonical_execution": {
            "present_attested": 1, "pending": 8, "blocked": 0, "failed": 0,
        },
    }), "pending"),
    ("borrow", {
        "status": "blocked", "provider_verified": False,
        "reason_code": "borrow_not_submitted",
    }, "blocked"),
    ("opening-sync", {"identities": [{
        "status": "partial", "identity_id": "PRIVATE_PROFILE",
        "canonical_execution": {"present_attested": 1, "pending": 8, "failed": 0},
    }]}, "partial"),
    ("deep-sync", {"identities": [
        {"status": "success", "identity_id": "PROFILE_1"},
        {"status": "partial", "identity_id": "PROFILE_2"},
    ]}, "partial"),
])
def test_taskrunner_propagates_closed_nonfatal_status_to_activity_without_new_events(
    caplog, kind, receipt, expected,
):
    runner, displayed, flush, logger = _runner()
    delivered = []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Source privée", lambda: receipt, ok=delivered.append,
                kind=kind, origin="automatic",
            )
            flush()
        assert delivered == [receipt]
        finishes = [args for op, args in displayed if op == "finished"]
        assert len(finishes) == 1
        assert finishes[0][3] is False
        assert len(finishes[0]) == 6
        assert finishes[0][-1] == expected
        assert f"business_status={expected}" in caplog.text
        assert "PRIVATE_" not in caplog.text
    finally:
        runner.close()


@pytest.mark.parametrize(("identities", "expected"), [
    ([{"status": "success"}], "success"),
    ([{"status": "failed"}, {"status": "success"}], "failed"),
    ([{"status": "cancelled"}], "cancelled"),
    ([{"status": "partial"}], "partial"),
    ([{"status": "unknown"}], "failed"),
])
def test_opening_status_priority_preserves_failure_and_cancellation(
    identities, expected,
):
    runner, _displayed, _flush, _logger = _runner()
    try:
        result = {"identities": identities}
        assert runner._source_sync_outcome(result, "opening-sync") == expected
        assert runner._source_sync_outcome(result, "deep-sync") == expected
        assert runner._source_sync_outcome({"identities": []}, "opening-sync") == "success"
    finally:
        runner.close()


def test_opening_scope_correlates_partial_without_inventing_task_ref(caplog):
    with caplog.at_level(logging.INFO, logger="cifereca"):
        with diagnostic_task_scope():
            ref = diagnostic_task_reference()
            diagnostic_task_business_result({
                "identities": [{"status": "partial", "identity_id": "PRIVATE_PROFILE"}],
            }, operation_kind="opening-sync")
        assert f"task_ref={ref} status=failed" in caplog.text
        assert "PRIVATE_PROFILE" not in caplog.text
        caplog.clear()
        with diagnostic_task_scope():
            diagnostic_task_business_result({
                "identities": [{"status": "partial"}],
            }, operation_kind="opening-sync")
        assert "TRACE task correlation" not in caplog.text
        caplog.clear()
        with diagnostic_task_scope():
            ref = diagnostic_task_reference()
            diagnostic_task_business_result({
                "identities": [{"status": "cancelled"}],
            }, operation_kind="deep-sync")
        assert f"task_ref={ref} status=cancelled" in caplog.text



def test_stale_profile_source_scope_without_core_call_is_not_success(caplog):
    runner, displayed, flush, logger = _runner()
    calls = []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            # The UI's exact source scope was invalidated before execution:
            # its worker returns None without entering the source broker.
            runner.submit(
                "Ancien profil", lambda: None, ok=calls.append,
                kind="source-sync", origin="user",
                success_toast="Succès inventé",
            )
            flush()
        assert calls == [None]  # UI refresh owns its stale-scope guard.
        finish = [args for name, args in displayed if name == "finished"]
        assert finish[0][3] is False
        assert finish[0][-1] == "cancelled"
        assert "business_status=cancelled" in caplog.text
        assert "status=success" not in caplog.text
        assert "Succès inventé" not in caplog.text
    finally:
        runner.close()



def test_cancelled_source_receipt_has_cancelled_task_ref_not_failure(caplog):
    with caplog.at_level(logging.INFO, logger="cifereca"):
        for facts in (
            {"status": "cancelled"},
            {"reason": "source_sync_cancelled"},
            {"sources": {"source-a": {"status": "cancelled"}}},
        ):
            caplog.clear()
            with diagnostic_task_scope():
                ref = diagnostic_task_reference()
                diagnostic_task_business_result(SyncResult(ok=False, facts=facts))
            assert f"task_ref={ref} status=cancelled" in caplog.text
            assert f"task_ref={ref} status=failed" not in caplog.text
        caplog.clear()
        with diagnostic_task_scope():
            ref = diagnostic_task_reference()
            diagnostic_task_business_result(SyncResult(
                ok=False, facts={"status": "partial"},
            ))
        assert f"task_ref={ref} status=failed" in caplog.text
