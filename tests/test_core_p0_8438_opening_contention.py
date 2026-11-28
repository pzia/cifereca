"""#8438: opening worker preserves closed lifecycle signals without private text."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.sync_coordination import opening_sync
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.plugin_api import Cancelled
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_1346_kobo_on_start_inventory_gui import _startup_window


def test_opening_worker_reports_sanitized_real_contention(tmp_path):
    path = tmp_path / "PRIVATE_SENTINEL.lock"
    core = NS(_opening_sync_locked=lambda **kw: pytest.fail("entered locked work"))
    r, _, run_opening = _startup_window()
    r.window.service.opening_sync = lambda **kw: opening_sync(
        core, lock_factory=InterProcessLock, lock_path=lambda: path, **kw
    )
    with InterProcessLock(path):
        run_opening(r.window)
        with pytest.raises(LockBusyError) as caught:
            r.jobs.pop().work(lambda *_: None)
    assert "PRIVATE_SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("error,expected", [
    (Cancelled("PRIVATE_SENTINEL"), Cancelled),
    (ProviderConfigurationError("PRIVATE_SENTINEL"), RuntimeError),
])
def test_opening_worker_redacts_cancel_and_provider_failure(error, expected):
    r, _, run_opening = _startup_window()
    def fail(**kw):
        raise error
    r.window.service.opening_sync = fail
    run_opening(r.window)
    with pytest.raises(expected) as caught:
        r.jobs.pop().work(lambda *_: None)
    assert type(caught.value) is expected
    assert "PRIVATE_SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("kind", ["busy", "cancelled", "failed"])
@pytest.mark.parametrize("deep", [False, True])
def test_opening_lifecycle_reaches_taskrunner_and_diagnostics(tmp_path, caplog, kind, deep):
    import logging
    from tests.test_core_p0_981_redacted_task_traces import _runner

    r, _, run_opening = _startup_window()
    runner, displayed, flush, logger = _runner()
    window = r.window
    window.runner = runner
    for name in ("_task_started", "_task_finished", "_task_progress_for_task", "show_error"):
        setattr(window, name, getattr(runner.window, name))
    runner.window = window
    diagnostics = []
    window.diagnostics.add = lambda *a, **kw: diagnostics.append((a, kw))
    path = tmp_path / "opening.lock"
    core = NS(_opening_sync_locked=lambda **kw: pytest.fail("entered locked work"))

    def fail(**kw):
        if kind == "busy":
            return opening_sync(core, lock_factory=InterProcessLock,
                                lock_path=lambda: path, **kw)
        if kind == "cancelled":
            raise Cancelled("fixture")
        raise ProviderConfigurationError("fixture")

    window.service.opening_sync = fail
    window._source_inventory_startup_pending = not deep
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            with InterProcessLock(path):
                run_opening(window, deep=deep)
                flush()
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1 and finished[0][3] is False
        if kind != "failed":
            assert finished[0][-1] == kind
        assert f"business_status={kind}" in caplog.text
        assert ("TRACE task failure" in caplog.text) is (kind == "failed")
        opening = [(args, kw) for args, kw in diagnostics
                   if args[:2] == ("SYNC", "opening sync")]
        reason = {"busy": "operation_busy", "cancelled": "opening_sync_cancelled",
                  "failed": "opening_sync_failed"}[kind]
        assert opening == [(("SYNC", "opening sync",
                             "error" if kind == "failed" else "warning"),
                            {"detail": {"reason": reason}})]
        assert not r.jobs
        window.show_error.assert_not_called()
    finally:
        runner.close()
