"""#5178: only a typed, task-scoped background lock collision is nonmodal.

Three concurrent worker completions are synthetic and independent from any
provider/SSO state. No private failure string enters UI/diagnostics.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.feedback_dialog_controller import show_error
from cifereca.gui.ui_dialog_observation import (
    observe_background_busy_task, task_dialog_context,
)
from cifereca.locking import LockBusyError
from cifereca.builtin_ui.chrome import Titlebar
from tests.test_core_p0_981_redacted_task_traces import _runner
from tests.test_p1_4277_unavailable_dialog_observations import FakeDialog


SECRET = "PRIVATE_LOCK_PATH_5178"
URL = "https://private.invalid/?token=" + SECRET


def _ui_runner():
    runner, displayed, flush, logger = _runner()
    dialogs, diagnostics, toasts = [], [], []
    adw = NS(MessageDialog=NS(new=lambda _w, title, message: FakeDialog(
        title, message, dialogs,
    )))
    window = runner.window
    window.diagnostics = NS(add=lambda *a, **kw: diagnostics.append((a, kw)))
    window.toast = lambda text, duration: toasts.append((text, duration))
    window._functional_error_message = lambda value: value
    window.show_error = lambda message, title="Action impossible": show_error(
        window, message, title, Adw=adw,
    )
    return runner, displayed, flush, logger, dialogs, diagnostics, toasts


def _locked():
    try:
        raise BlockingIOError(SECRET)
    except BlockingIOError as cause:
        raise LockBusyError(URL) from cause


def test_three_background_lock_failures_keep_activity_and_have_no_modal(caplog):
    runner, displayed, flush, logger, dialogs, diagnostics, toasts = _ui_runner()
    try:
        def on_error(_error):
            # An existing provider-specific callback attempts to open a
            # generic modal; TaskRunner's exact context must suppress it.
            runner.window.show_error("Traitement indisponible")
        for _ in range(3):
            runner.submit(
                "Private background task " + URL, _locked,
                kind="maintenance", origin="maintenance", err=on_error,
            )
        flush()
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 3
        assert all(args[3] is False and args[4] ==
                   "Une autre opération est déjà en cours" for args in finished)
        assert dialogs == []
        assert len(toasts) == 3
        assert all(URL not in repr(row) for row in toasts)
        busy = [row[1]["detail"] for row in diagnostics
                if row[0] == ("UI", "background task contention", "warning")]
        assert sorted(row["task_ref"] for row in busy) == [1, 2, 3]
        assert all(row == {
            "schema_version": 1, "code": "lock_busy", "task_ref": index,
            "task_classification": "background_task",
        } for index, row in enumerate(sorted(busy, key=lambda r: r["task_ref"]), 1))
        assert URL not in repr(diagnostics)
        assert not hasattr(runner.window, "_ui_dialog_observation_context")
    finally:
        runner.close()


def test_user_lock_contention_remains_actionable_and_correlated():
    runner, displayed, flush, _, dialogs, diagnostics, toasts = _ui_runner()
    try:
        runner.submit("Action explicite", _locked, kind="maintenance",
                      origin="user", err=lambda _exc:
                      runner.window.show_error("Traitement indisponible"))
        flush()
        assert len([row for row in dialogs if row[0] == "present"]) == 1
        row = next(record[1]["detail"] for record in diagnostics
                   if record[0] == ("UI", "action unavailable dialog", "warning"))
        assert row["task_ref"] == 1
        assert row["task_classification"] == "user_action"
        assert row["reason_code"] == "lock_busy"
        assert URL not in repr(dialogs) + repr(diagnostics)
    finally:
        runner.close()


def test_message_mentioning_busy_without_proof_is_not_classified():
    runner, displayed, flush, _, dialogs, diagnostics, _toasts = _ui_runner()
    try:
        assert runner._task_has_typed_lock_busy(RuntimeError("LockBusyError: " + URL)) is False
        assert runner._task_has_typed_lock_busy(BlockingIOError(URL)) is False
        assert runner._task_has_typed_lock_busy(LockBusyError(URL)) is True
        runner.submit("Unproven", lambda: (_ for _ in ()).throw(
            RuntimeError("LockBusyError: " + URL)),
            origin="maintenance",
            err=lambda _exc: runner.window.show_error("Traitement indisponible"))
        flush()
        assert [record for record in diagnostics
                if record[0] == ("UI", "background task contention", "warning")] == []
        assert len([row for row in dialogs if row[0] == "present"]) == 1
        assert URL not in repr(dialogs) + repr(diagnostics)
    finally:
        runner.close()


def test_stale_task_context_and_invalid_scope_never_suppress_ui():
    runner, _, _, _, dialogs, diagnostics, _toasts = _ui_runner()
    try:
        window = runner.window
        for bad in (0, -1, True, 2**53, URL):
            with task_dialog_context(
                window, task_ref=bad, classification="background_task",
                reason_code="lock_busy", suppress_busy_modal=True,
            ):
                window.show_error("Traitement indisponible")
        with task_dialog_context(
            window, task_ref=42, classification="user_action",
            reason_code="lock_busy", suppress_busy_modal=True,
        ):
            window.show_error("Traitement indisponible")
        assert len([row for row in dialogs if row[0] == "present"]) == 6
        assert not hasattr(window, "_ui_dialog_observation_context")
    finally:
        runner.close()


def test_old_bookings_preference_is_inert_without_mutating_saved_config():
    stored = ["search.open", "bookings.open", "library.open",
              "bookings.open", "unrelated.obsolete"]
    settings = {"actions": list(stored)}
    titlebar = Titlebar().activate(NS(configuration=lambda: settings))
    assert titlebar.selected_actions() == [
        "search.open", "library.open", "unrelated.obsolete",
    ]
    assert settings == {"actions": stored}  # read-only compatibility
    assert titlebar.selected_actions() == [
        "search.open", "library.open", "unrelated.obsolete",
    ]
