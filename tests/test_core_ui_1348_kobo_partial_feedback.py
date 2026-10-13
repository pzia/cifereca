"""#1348 — GTK must not display an incomplete Kobo acquisition as a success.

Exercise the real sync_controller callback without requiring a GTK display.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from tests.test_core_ui_1132_kobo_progress_close_gate import _method


def _finish(summary, *, manual=True):
    run, queued = _method("run_kobo_backup")
    submissions = []
    activity = []
    renders = []
    host = NS(
        _closed=False,
        service=NS(sync_kobo_backup=lambda **_kw: summary),
        runner=NS(submit=lambda *args, **kwargs: submissions.append((args, kwargs))),
        _render_kobo_loans=lambda: renders.append("kobo"),
        _activity_result=lambda message, **kwargs: activity.append((message, kwargs)),
    )
    run(host, manual=manual)
    assert len(submissions) == 1
    args, kwargs = submissions[0]
    assert kwargs["kind"] == "kobo-acquisition"
    args[2](summary)
    assert renders == ["kobo"]
    assert queued == []
    return activity


@pytest.mark.parametrize("status,remaining,attention,errors,expected", [
    ("partial", 202, 0, 0, "partielle"),
    ("aborted", 202, 0, 0, "interrompue"),
    ("cancelled", 202, 0, 0, "annulée"),
    ("attention_required", 0, 3, 0, "action requise"),
    ("failed", 0, 0, 0, "en échec"),
    ("unexpected_provider_text", 0, 0, 0, "état non confirmé"),
])
def test_manual_kobo_noncomplete_status_is_persistent_not_success(
    status, remaining, attention, errors, expected,
):
    result = _finish({
        "status": status, "downloaded": 0, "pending": 0,
        "unchanged": 0, "skipped": 0, "remaining": remaining,
        "attention_required": attention, "errors": errors,
    })
    assert len(result) == 1
    label, state = result[0]
    assert expected in label
    if remaining:
        assert f"{remaining} restant(s)" in label
    assert state == {"success": False, "persistent": True}
    assert "unexpected_provider_text" not in label


def test_kobo_early_success_inference_rejected_on_remaining_without_errors():
    result = _finish({
        "status": "complete", "downloaded": 116, "pending": 0,
        "unchanged": 0, "skipped": 0, "remaining": 406,
        "attention_required": 0, "errors": 0,
    })
    label, state = result[0]
    assert "partielle" in label
    assert "406 restant(s)" in label
    assert state == {"success": False, "persistent": True}


@pytest.mark.parametrize("unexpected", [
    {"pending": 1}, {"reconcile_pending": 1},
    {"malformed_manifest_items": 1},
])
def test_kobo_complete_with_outstanding_work_is_not_green(unexpected):
    summary = {
        "status": "complete", "downloaded": 0, "pending": 0,
        "unchanged": 0, "skipped": 0, "remaining": 0,
        "attention_required": 0, "errors": 0,
        **unexpected,
    }
    label, state = _finish(summary)[0]
    assert "partielle" in label
    assert state == {"success": False, "persistent": True}


def test_manual_kobo_truly_complete_is_success_and_nonpersistent():
    label, state = _finish({
        "status": "complete", "downloaded": 3, "pending": 0,
        "unchanged": 0, "skipped": 0, "remaining": 0,
        "attention_required": 0, "errors": 0,
    })[0]
    assert "terminée" in label
    assert state == {"success": True, "persistent": False}


def test_auto_kobo_zero_count_partial_still_warns_without_claiming_success():
    activity = _finish({
        "status": "partial", "downloaded": 0,
        "pending": 0, "attention_required": 0, "errors": 0,
        "remaining": 0, "malformed_manifest_items": 2,
    }, manual=False)
    assert len(activity) == 1
    label, state = activity[0]
    assert "automatique (partielle)" in label
    assert state == {"success": False, "persistent": True}


def test_auto_kobo_complete_empty_manifest_remains_silent():
    assert _finish({
        "status": "complete", "downloaded": 0, "pending": 0,
        "attention_required": 0, "errors": 0, "remaining": 0,
    }, manual=False) == []


def test_auto_kobo_completed_acquisitions_still_show_success():
    activity = _finish({
        "status": "complete", "downloaded": 2, "pending": 0,
        "attention_required": 0, "errors": 0, "remaining": 0,
    }, manual=False)
    assert len(activity) == 1
    assert "automatique (terminée)" in activity[0][0]
    assert activity[0][1] == {"success": True, "persistent": False}
