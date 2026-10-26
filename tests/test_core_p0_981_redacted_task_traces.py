"""#981/#1741: TRACE never persists account labels or arbitrary plugin task kinds."""
from __future__ import annotations

import ast
from concurrent.futures import Future
import itertools
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.task_queue import TaskPriority
from cifereca.plugin_api import UserFacingError, AttentionRequired


def _runner():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/task_runner.py"
    task = next(
        node for node in ast.parse(source.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == "TaskRunner"
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0), task],
        type_ignores=[],
    ))
    callbacks = []
    displayed = []

    class Pool:
        def __init__(self, **kwargs):
            pass

        def submit(self, _priority, function):
            future = Future()
            try:
                future.set_result(function())
            except BaseException as exc:
                future.set_exception(exc)
            return future

        def shutdown(self, **kwargs):
            pass

    logger = logging.getLogger("test-981-trace-redaction")
    scope = dict(
        itertools=itertools, threading=threading, time=time,
        GLib=NS(idle_add=lambda fn, *args: callbacks.append((fn, args))),
        _TRACE_LOG=logger, PriorityExecutor=Pool, TaskPriority=TaskPriority,
        Future=Future,
        UserFacingError=UserFacingError, AttentionRequired=AttentionRequired,
    )
    exec(compile(module, str(source), "exec"), scope)
    window = NS(
        _closed=False,
        _task_started=lambda *args: displayed.append(("started", args)),
        _task_finished=lambda *args: displayed.append(("finished", args)),
        _task_progress_for_task=Mock(),
        show_error=Mock(),
    )

    def flush():
        while callbacks:
            fn, args = callbacks.pop(0)
            fn(*args)

    return scope["TaskRunner"](window), displayed, flush, logger


def test_trace_never_logs_private_ui_label_or_untrusted_kind(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        secret = "https://private.invalid/?token=PRIVATE_BEARER"
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Compte privé : " + secret, lambda: 42,
                kind="provider:" + secret, origin="user",
            )
            flush()
        text = caplog.text
        assert secret not in text and "PRIVATE_BEARER" not in text
        assert "label=" not in text
        assert "kind=other" in text
        assert "status=success" in text
        assert displayed[0][1][2] == "Compte privé : " + secret
        assert displayed[0][1][4] == "provider:" + secret
        assert displayed[1][0] == "finished"
    finally:
        runner.close()


def test_coalesced_task_does_not_log_private_second_label(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        secret = "Bearer SECRET_SECOND_TASK"
        calls = []
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Public", lambda: calls.append(1),
                          kind="opening-sync", coalesce_key="same",
                          origin="automatic")
            runner.submit(secret, lambda: calls.append(2),
                          kind="opening-sync", coalesce_key="same",
                          origin="automatic")
            flush()
        assert calls == [1]
        assert secret not in caplog.text
        assert "TRACE task coalesced" in caplog.text
        assert "kind=opening-sync" in caplog.text
        assert len(displayed) == 2
    finally:
        runner.close()


def test_rejected_task_kind_is_redacted_without_breaking_feedback(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        private = "PRIVATE_PASSWORD"
        def reject(_priority, _function):
            raise RuntimeError(private)
        runner.pool.submit = reject
        errors = []
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Sensitive: " + private, lambda: None,
                          kind="private:" + private, err=errors.append)
            flush()
        assert private not in caplog.text
        assert "kind=other" in caplog.text
        assert "status=rejected" in caplog.text
        assert len(errors) == 1
        assert str(errors[0]) == "Traitement indisponible"
        assert displayed[-1][0] == "finished"
        assert displayed[-1][1][3] is False
    finally:
        runner.close()


def test_core_gui_navigation_kinds_are_named_in_trace_without_private_labels(caplog):
    """Known MainWindow operations must not be indistinguishable as 'other'."""
    runner, displayed, flush, logger = _runner()
    private = "https://private.invalid/profile?token=SECRET_NAVIGATION"
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            for kind in ("history", "bookings", "search-known"):
                runner.submit(
                    "Compte " + private, lambda: None, kind=kind, origin="user",
                )
                flush()
        for kind in ("history", "bookings", "search-known"):
            assert f"kind={kind}" in caplog.text
        assert "kind=other" not in caplog.text
        assert private not in caplog.text
        assert len([event for event, _ in displayed if event == "finished"]) == 3
    finally:
        runner.close()
