"""P1 #3373: real GTK task runner correlation, without a live GUI/provider.

The task runner is exercised through the existing deterministic GLib seam.
"""
from __future__ import annotations

import logging

from tests.test_core_p0_981_redacted_task_traces import _runner


def test_automatic_failure_is_correlated_and_nonmodal_with_successful_neighbor(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        def failure():
            raise RuntimeError("HTTP ?token=DO_NOT_LOG") from OSError("Bearer SECRET")

        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Opening refresh", failure, kind="opening-sync",
                          origin="automatic")
            runner.submit("Source refresh", lambda: {"ok": True},
                          kind="source-sync", origin="automatic")
            flush()

        states = [event for event, _ in displayed if event == "finished"]
        outcomes = [details for event, details in displayed if event == "finished"]
        assert len(states) == 2
        assert outcomes[0][3:] == (False, "Traitement indisponible")
        assert outcomes[1][3:] == (True, None)
        assert not runner.window.show_error.called
        assert "TRACE task failure id=1 kind=opening-sync origin=automatic" in caplog.text
        assert "phase=worker exception=RuntimeError cause=OSError" in caplog.text
        assert "TRACE task outcome id=2 kind=source-sync origin=automatic status=success" in caplog.text
        assert "token=DO_NOT_LOG" not in caplog.text
        assert "Bearer SECRET" not in caplog.text
    finally:
        runner.close()


def test_explicit_failure_still_displays_error_and_keeps_same_task_id(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        with caplog.at_level(logging.ERROR, logger=logger.name):
            runner.submit("Explicit borrow", lambda: (_ for _ in ()).throw(ValueError("bad")),
                          kind="loan-acquisition", origin="user")
            flush()
        assert runner.window.show_error.call_args.args == ("Traitement indisponible",)
        assert "id=1 kind=loan-acquisition origin=user phase=worker exception=ValueError" in caplog.text
        assert [e for e, _ in displayed] == ["started", "finished"]
    finally:
        runner.close()


def test_automatic_enqueue_rejection_does_not_open_generic_modal(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        def reject(*args):
            raise RuntimeError("password=SECRET_PWD")
        runner.pool.submit = reject
        with caplog.at_level(logging.ERROR, logger=logger.name):
            runner.submit("automatic task", lambda: 1,
                          kind="provider-connect", origin="automatic")
            flush()
        assert not runner.window.show_error.called
        assert [e for e, _ in displayed] == ["started", "finished"]
        assert displayed[-1][1][3:] == (False, "Traitement indisponible")
        assert "phase=enqueue exception=RuntimeError cause=none" in caplog.text
        assert "SECRET_PWD" not in caplog.text
        assert runner._count == 0
    finally:
        runner.close()


def test_auto_custom_handler_keeps_its_existing_failure_routing():
    runner, _displayed, flush, _logger = _runner()
    errors = []
    try:
        runner.submit("automatic task", lambda: (_ for _ in ()).throw(
            ValueError("source-specific error"),
        ), kind="source-sync", origin="automatic", err=errors.append)
        flush()
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert not runner.window.show_error.called
    finally:
        runner.close()


def test_success_worker_with_failing_gtk_callback_is_not_silent_success(caplog):
    runner, displayed, flush, logger = _runner()
    activity_results = []
    runner.window._activity_result = (
        lambda text, **kwargs: activity_results.append((text, kwargs))
    )
    try:
        def ui_failure(_value):
            raise LookupError("cookie=NEVER_LOG")
        with caplog.at_level(logging.ERROR, logger=logger.name):
            runner.submit("Opening refresh", lambda: {"sync": "ok"},
                          ok=ui_failure, kind="opening-sync",
                          origin="automatic")
            flush()
        assert [e for e, _ in displayed] == ["started", "finished"]
        assert displayed[-1][1][3] is True  # worker completed
        assert activity_results == [(
            "Traitement indisponible",
            {"success": False, "task_id": 1},
        )]
        assert not runner.window.show_error.called
        assert "id=1 kind=opening-sync origin=automatic phase=gtk_ok_callback exception=LookupError" in caplog.text
        assert "NEVER_LOG" not in caplog.text
    finally:
        runner.close()


def test_gtk_error_callback_failure_is_diagnostic_not_provider_failure(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        def worker():
            raise OSError("remote failure")
        def handler(_payload):
            raise LookupError("Bearer NEVER_LOG")
        with caplog.at_level(logging.ERROR, logger=logger.name):
            runner.submit("Source sync", worker, err=handler,
                          kind="source-sync", origin="automatic")
            flush()
        assert [x for x, _ in displayed] == ["started", "finished"]
        assert displayed[-1][1][3] is False
        assert not runner.window.show_error.called
        assert "phase=worker exception=OSError" in caplog.text
        assert "phase=gtk_error_callback exception=LookupError" in caplog.text
        assert "NEVER_LOG" not in caplog.text
    finally:
        runner.close()
