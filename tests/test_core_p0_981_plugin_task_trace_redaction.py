"""P0 #981 / P1 #1741: plugin task traces must never persist private input."""
from __future__ import annotations

import logging
import threading

import pytest

from cifereca.plugin_api import Cancelled
from cifereca.task_runtime import TaskBrokerImpl


@pytest.fixture
def broker():
    runtime = TaskBrokerImpl(object(), max_workers=1)
    yield runtime
    runtime.close()


def test_untrusted_plugin_id_kind_and_label_never_enter_trace(caplog, broker):
    private = "https://private.example/account?access_token=TOP_SECRET"
    scoped = broker.for_plugin("provider:" + private)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        task = scoped.submit(
            lambda _control: 42, kind="task:" + private,
            label="Compte client " + private,
        )
        assert task.result(timeout=2) == 42
    assert private not in caplog.text
    assert "TOP_SECRET" not in caplog.text
    assert "label=" not in caplog.text
    assert "TRACE plugin-task begin" in caplog.text
    assert "plugin=plugin" in caplog.text
    assert "kind=other" in caplog.text
    assert "status=success" in caplog.text


def test_core_task_keeps_safe_log_category_and_operation_result(caplog, broker):
    with caplog.at_level(logging.INFO, logger="cifereca"):
        task = broker.submit(lambda _control: "ok", label="Private title", kind="core")
        assert task.result(timeout=2) == "ok"
    assert "plugin=core" in caplog.text
    assert "kind=core" in caplog.text
    assert "status=success" in caplog.text
    assert "Private title" not in caplog.text


def test_failing_plugin_operation_reports_failure_without_exception_text(caplog, broker):
    private = "Bearer PRIVATE_ACCOUNT_TOKEN"
    def fail(_control):
        raise RuntimeError(private)

    with caplog.at_level(logging.INFO, logger="cifereca"):
        task = broker.for_plugin("plugin.safe").submit(
            fail, label=private, kind="maintenance",
        )
        with pytest.raises(RuntimeError, match="PRIVATE_ACCOUNT_TOKEN"):
            task.result(timeout=2)
    assert private not in caplog.text
    assert "kind=maintenance" in caplog.text
    assert "status=failed" in caplog.text


def test_cooperative_cancel_reports_cancelled_without_privacy_leak(caplog, broker):
    started = threading.Event()
    release = threading.Event()
    private = "/home/reader/private/book.epub"

    def operation(control):
        started.set()
        assert release.wait(2)
        control.check_cancelled()

    with caplog.at_level(logging.INFO, logger="cifereca"):
        task = broker.for_plugin("plugin.safe").submit(
            operation, label=private, kind="plugin",
        )
        assert started.wait(2)
        task.cancel()
        release.set()
        with pytest.raises(Cancelled):
            task.result(timeout=2)
    assert private not in caplog.text
    assert "status=cancelled" in caplog.text
    assert "kind=plugin" in caplog.text


def test_progress_callback_is_still_delivered_unmodified(broker):
    actual = []
    private = "Titre du compte privé"

    def operation(control):
        control.report(current=1, total=2, message=private)
        return "done"

    task = broker.for_plugin("plugin.safe").submit(
        operation, kind="task", label=private,
        on_progress=lambda current, total, message: actual.append(
            (current, total, message)
        ),
    )
    assert task.result(timeout=2) == "done"
    assert actual == [(1, 2, private)]


@pytest.mark.parametrize("kind", ("diagnostics", "recommendations", "browser-recorder"))
def test_installed_plugin_task_kind_has_attributable_private_safe_trace(
    caplog, broker, kind,
):
    """An existing fixed kind remains visible; plugin labels never do."""
    private = "Bearer PRIVATE_PLUGIN_ACTIVITY"
    with caplog.at_level(logging.INFO, logger="cifereca"):
        task = broker.for_plugin("plugin.safe").submit(
            lambda _control: kind, kind=kind, label=private,
        )
        assert task.result(timeout=2) == kind
    assert f"kind={kind}" in caplog.text
    assert "kind=other" not in caplog.text
    assert private not in caplog.text
