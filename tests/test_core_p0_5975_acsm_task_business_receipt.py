"""P0 #5975/#5833: ACSM staging and identity cancellation are not imports.

The production TaskRunner is exercised headlessly, including the actual
manual ACSM GTK chooser/controller with deferred GLib delivery. No network,
ADEPT runtime, supplier loan or filesystem write is performed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner
from tests.test_core_ui_1132_acsm_import_chooser_controller import (
    _setup, _submit,
)


ACQUISITION = "manual-acsm:" + "a" * 32
ARTIFACT = "sha256:" + "b" * 64


def complete():
    return {
        "status": "complete", "acquisition_id": ACQUISITION,
        "artifact_id": ARTIFACT, "path": "/private/library/confirmed.epub",
    }


def unchanged():
    return {
        "status": "unchanged", "acquisition_id": ACQUISITION,
        "path": "/private/library/confirmed.epub",
    }


class HostileStatus(str):
    def __eq__(self, _other):
        raise AssertionError("status comparison reached untrusted subclass")


class HostileDict(dict):
    def get(self, _name, _default=None):
        raise AssertionError("status lookup reached untrusted subclass")


@pytest.mark.parametrize(("result", "state", "message"), [
    (complete(), "success", None),
    (unchanged(), "success", None),
    ({"status": "complete"}, "failed", "Import ACSM non confirmé"),
    ({"status": "unchanged"}, "failed", "Import ACSM non confirmé"),
    ({**complete(), "artifact_id": None}, "failed", "Import ACSM non confirmé"),
    ({**complete(), "artifact_id": "sha256:invalid"},
     "failed", "Import ACSM non confirmé"),
    ({**complete(), "artifact_id": HostileStatus("sha256:" + "b" * 64)},
     "failed", "Import ACSM non confirmé"),
    ({**unchanged(), "acquisition_id": "manual-acsm:invalid"},
     "failed", "Import ACSM non confirmé"),
    ({**unchanged(), "path": HostileStatus("/private/confirmed.epub")},
     "failed", "Import ACSM non confirmé"),
    ({**complete(), "path": "private\x00document"},
     "failed", "Import ACSM non confirmé"),
    ({"status": "attention_required", "code": "adept_profile_required"},
     "pending", "Import ACSM en attente — configuration DRM nécessaire"),
    ({"status": "attention_required", "code": "private-URL?token=SECRET"},
     "pending", "Import ACSM en attente — configuration DRM nécessaire"),
    ({"status": "cancelled"}, "cancelled", "Import ACSM annulé"),
    ({"status": "blocked"}, "blocked", "Import ACSM bloqué"),
    ({"status": "retryable_error", "secret": "PRIVATE_URL?token=SECRET"},
     "failed", "Import ACSM non confirmé"),
    ({"status": "failed"}, "failed", "Import ACSM non confirmé"),
    ({"status": "unexpected"}, "failed", "Import ACSM non confirmé"),
    ({"status": HostileStatus("complete")}, "failed", "Import ACSM non confirmé"),
    (HostileDict(status="complete"), "failed", "Import ACSM non confirmé"),
    ({"status": None}, "failed", "Import ACSM non confirmé"),
    (None, "failed", "Import ACSM non confirmé"),
    (True, "failed", "Import ACSM non confirmé"),
])
def test_task_receipt_never_claims_staged_or_cancelled_acsm_was_imported(
    caplog, result, state, message,
):
    runner, displayed, flush, logger = _runner()
    delivered, exceptions = [], []
    reporter = Mock()
    runner.window._activity_result = reporter
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Import ACSM", lambda: result,
                ok=delivered.append, err=exceptions.append,
                kind="acsm-import", origin="user",
                success_toast="ACSM acquis",
            )
            flush()
        finishes = [args for name, args in displayed if name == "finished"]
        assert len(finishes) == 1
        assert finishes[0][3] is (state == "success")
        assert finishes[0][4] == message
        if state in {"pending", "blocked", "cancelled"}:
            assert finishes[0][5] == state
        else:
            assert len(finishes[0]) == 5
        assert delivered == [result]
        assert exceptions == []
        if state == "success":
            reporter.assert_called_once()
        else:
            reporter.assert_not_called()
        traces = [
            entry.getMessage() for entry in caplog.records
            if "TRACE task outcome id=" in entry.getMessage()
        ]
        assert len(traces) == 1
        assert f"kind=acsm-import origin=user status={'success' if state == 'success' else 'failed'}" in traces[0]
        assert f"business_status={state}" in traces[0]
        assert "SECRET" not in caplog.text
        assert "PRIVATE_URL" not in caplog.text
    finally:
        runner.close()


def test_real_gtk_chooser_and_real_taskrunner_keep_missing_adept_as_pending():
    host, Gtk, events, created, _submissions, _identities = _setup()
    runner, displayed, flush, _logger = _runner()
    old_window = runner.window
    host._task_started = old_window._task_started
    host._task_finished = old_window._task_finished
    host._task_progress_for_task = old_window._task_progress_for_task
    host.runner = runner
    runner.window = host
    host.service.import_acsm = lambda _source, *, identity_id: {
        "status": "attention_required",
        "code": "adept_profile_required",
        "staging": "/private/acsm?token=SECRET",
    }
    try:
        _submit(host, Gtk, created)
        flush()
        assert [args[3:] for name, args in displayed if name == "finished"] == [
            (False, "Import ACSM en attente — configuration DRM nécessaire", "pending"),
        ]
        attention = [event for event in events if event[0] == "attention"]
        assert len(attention) == 1
        assert attention[0][2] == {"code": "adept_profile_required"}
        assert not any(event[0] in {"activity", "library", "error"}
                       for event in events)
        assert not any("SECRET" in str(event) for event in events)
    finally:
        runner.close()


def test_stale_exact_loan_import_task_reports_cancelled_without_success_toast():
    runner, displayed, flush, _logger = _runner()
    positives = Mock()
    runner.window._activity_result = positives
    try:
        runner.submit(
            "Import ADEPT du prêt", lambda: {"status": "cancelled"},
            kind="acsm-import", origin="user",
            success_toast="Indûment importé",
        )
        flush()
        finish = [args for name, args in displayed if name == "finished"]
        assert len(finish) == 1
        assert finish[0][3:] == (False, "Import ACSM annulé", "cancelled")
        positives.assert_not_called()
    finally:
        runner.close()


def test_unrelated_task_kind_preserves_legacy_business_receipt_semantics():
    runner, displayed, flush, _logger = _runner()
    try:
        runner.submit("Unrelated", lambda: {"status": "attention_required"},
                      kind="library", origin="user")
        flush()
        assert [args[3] for name, args in displayed if name == "finished"] == [True]
    finally:
        runner.close()
