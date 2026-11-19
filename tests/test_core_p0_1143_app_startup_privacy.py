"""P0 #1143: GTK entrypoint must never log private plugin IDs or tracebacks."""
from __future__ import annotations

import logging
import sys
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock

from cifereca import app


PRIVATE = "https://private.invalid/LOAN/borrow.acsm?auth=SECRET_STARTUP"
IDENTITY = "PRIVATE_IDENTITY_2026"


def test_gtk_import_failure_never_stringifies_exception(capsys):
    class HostileError(RuntimeError):
        def __str__(self):
            raise AssertionError("private exception was stringified")
    status = app._gtk_import_error(HostileError(PRIVATE))
    captured = capsys.readouterr()
    assert status == 3
    assert "GTK 4" in captured.err
    assert "SECRET_STARTUP" not in captured.err
    assert "dépendances graphiques indisponibles" in captured.err


def _fake_gui(monkeypatch, *, raises=False):
    gi = ModuleType("gi")
    gi.require_version = Mock()
    repo = ModuleType("gi.repository")
    class FakeApplication:
        def __init__(self, **_kwargs):
            pass
        def run(self, _argv):
            if raises:
                raise RuntimeError(PRIVATE)
            return 0
    adw = NS(Application=FakeApplication)
    gio = NS(Application=NS(do_shutdown=Mock()))
    repo.Adw = adw
    repo.Gio = gio
    gi.repository = repo
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    gui = ModuleType("cifereca.gui")
    gui.MainWindow = Mock()
    monkeypatch.setitem(sys.modules, "cifereca.gui", gui)
    core = NS(
        config=NS(), library=NS(current_identity=NS(identity_id=IDENTITY)),
        diagnostics=NS(level="normal"),
        storage=NS(schema_version=lambda: 1),
        close=Mock(),
    )
    monkeypatch.setattr(app.CiferecaCore, "open", Mock(return_value=core))
    monkeypatch.setattr(app, "set_diagnostics_level", Mock())
    logger = logging.getLogger("cifereca.tests.app-startup")
    monkeypatch.setattr(app, "configure_logging", Mock(return_value=logger))
    return logger, core


def test_successful_startup_logs_counts_not_identity_or_forced_plugin_id(
    monkeypatch, caplog,
):
    logger, core = _fake_gui(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "cifereca", "--force-plugin", PRIVATE, "--no-plugins",
    ])
    with caplog.at_level(logging.INFO, logger=logger.name):
        assert app.main() == 0
    assert "forced_plugin_count=1" in caplog.text
    assert "TRACE startup core-open end schema=1" in caplog.text
    assert PRIVATE not in caplog.text
    assert IDENTITY not in caplog.text
    app.CiferecaCore.open.assert_called_once_with(
        no_plugins=True, force_plugins=(PRIVATE,), activate_plugins=False,
    )


def test_fatal_gtk_exception_is_fixed_code_without_traceback(
    monkeypatch, caplog,
):
    logger, core = _fake_gui(monkeypatch, raises=True)
    monkeypatch.setattr(sys, "argv", ["cifereca"])
    with caplog.at_level(logging.INFO, logger=logger.name):
        assert app.main() == 1
    assert "Fatal GTK GUI error code=gtk_runtime_failed" in caplog.text
    assert PRIVATE not in caplog.text
    assert "Traceback" not in caplog.text
    assert IDENTITY not in caplog.text

def test_desktop_composition_uses_one_admission_budget(monkeypatch):
    from cifereca.task_admission import SharedTaskAdmission
    _fake_gui(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cifereca"])
    assert app.main() == 0
    opts = app.CiferecaCore.open.call_args.kwargs
    budget = opts["notice_work_admission"]
    assert type(budget) is SharedTaskAdmission
    assert budget.max_inflight == 16 and budget.reserve_user_slots == 3
    assert "notice_replay_registry" not in opts


def test_desktop_notice_hint_dispatches_real_retained_work_and_stops(tmp_path):
    from cifereca.task_admission import SharedTaskAdmission
    from cifereca.core import notice_c_job_journal as journal
    from cifereca.core.notice_c_exact_recompute import request_exact_public_recompute
    from cifereca.core.notice_c_cover_index import (
        persist_exact_public_search_cover, read_consolidated_notice_cover,
    )
    from tests.test_core_p0_4982_exact_public_recompute import source
    from tests.test_core_p0_4982_notice_work_runtime import open_core

    budget = SharedTaskAdmission(max_inflight=16, reserve_user_slots=3)
    events = []
    with open_core(tmp_path, notice_work_admission=budget) as core:
        old = persist_exact_public_search_cover(core.storage, source(core.storage, "Ancien", 10))
        source(core.storage, "Nouveau", 11)
        ref = request_exact_public_recompute(
            core.storage, old.document, now=100, expected_generation=0,
        )
        def observer(document, epoch):
            assert journal.read_job(core.storage, ref).state == "current"
            events.append((document, epoch))
        win = NS(_closed=False, document_covers=NS(refresh_committed_source_in_worker=observer))
        app._bind_desktop_notice_work(core, win)
        assert core.brokers["tasks"]._executor is None
        assert not events
        handle = win._notice_work_tick()
        assert handle is not None
        worker, = handle.result(timeout=10).handles
        assert worker.result(timeout=10) == "current"
        assert events == [(old.document, old.epoch + 1)]
        assert core.notice_work.documentary_revision() == 1
        assert read_consolidated_notice_cover(core.storage, old.document).sources[0].public_bibliography.title == "Nouveau"
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []
        win._closed = True
        assert win._notice_work_tick() is None
    win._closed = False
    assert win._notice_work_tick() is None



def test_notice_wakeup_is_one_scoped_source_and_stops_once():
    calls, removed, scheduled = [], [], []
    win = NS(_closed=False, _notice_work_tick=lambda: calls.append("hint"))
    def schedule(seconds, callback):
        scheduled.append((seconds, callback))
        return 42
    for _ in range(2):
        app._start_notice_work_wakeup(win, timeout_add_seconds=schedule,
                                      source_remove=removed.append)
    assert len(scheduled) == 1 and scheduled[0][0] == 5
    pulse = scheduled[0][1]
    assert calls == []
    assert pulse() is True and calls == ["hint"]
    app._stop_notice_work_wakeup(win)
    app._stop_notice_work_wakeup(win)
    assert removed == [42]
    assert pulse() is False and calls == ["hint"]
    inert = NS(_closed=False)
    app._start_notice_work_wakeup(inert, timeout_add_seconds=schedule,
                                  source_remove=removed.append)
    assert len(scheduled) == 1


def test_idle_wakeup_revisits_future_durable_job_without_user_action(tmp_path):
    from threading import Event
    from cifereca.task_admission import SharedTaskAdmission
    from cifereca.core import notice_c_job_journal as journal
    from cifereca.core.notice_c_exact_recompute import request_exact_public_recompute
    from cifereca.core.notice_c_cover_index import (
        persist_exact_public_search_cover, read_consolidated_notice_cover,
    )
    from tests.test_core_p0_4982_exact_public_recompute import source
    from tests.test_core_p0_4982_notice_work_runtime import open_core

    budget = SharedTaskAdmission(max_inflight=16, reserve_user_slots=3)
    now = [100]
    committed = Event()
    scheduled, removed = [], []
    def schedule(seconds, callback):
        scheduled.append(callback)
        return 42

    with open_core(tmp_path, notice_work_admission=budget) as core:
        runtime = core.notice_work
        runtime._pump._clock = runtime._pump._bridge._clock = lambda: now[0]
        old = persist_exact_public_search_cover(core.storage, source(core.storage, "Ancien", 10))
        source(core.storage, "Nouveau", 11)
        ref = request_exact_public_recompute(
            core.storage, old.document, now=100, expected_generation=0,
        )
        lease = journal.claim_job(core.storage, ref, now=100, lease_seconds=30)
        journal.fail_job(core.storage, lease, now=100, transient=True)
        assert journal.read_job(core.storage, ref).due == 101
        win = NS(_closed=False, document_covers=NS(
            refresh_committed_source_in_worker=lambda *_: committed.set()))
        app._bind_desktop_notice_work(core, win)
        initial = win._notice_work_tick()
        assert initial.result(timeout=10).handles == ()
        assert journal.read_job(core.storage, ref).attempts == 1
        app._start_notice_work_wakeup(win, timeout_add_seconds=schedule,
                                     source_remove=removed.append)
        now[0] = 105
        # Only the idle loop driver wakes the persisted retry.
        assert scheduled[0]() is True
        assert committed.wait(10)
        receipt = journal.read_job(core.storage, ref)
        assert receipt.state == "current" and receipt.attempts == 2
        assert read_consolidated_notice_cover(core.storage, old.document).sources[0].public_bibliography.title == "Nouveau"
        win._closed = True
        assert scheduled[0]() is False
        app._stop_notice_work_wakeup(win)
        assert removed == []  # GLib removes a callback returning False
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_documentary_refresh_coalesces_on_existing_heartbeat(monkeypatch):
    from cifereca.gui import library_refresh

    calls, scheduled, version = [], [], [0]
    runtime = NS(bind_committed_observer=lambda _: True,
                 request_tick=lambda: calls.append("hint"),
                 documentary_revision=lambda: version[0])
    window = NS(_closed=False, document_covers=NS(
        refresh_committed_source_in_worker=lambda *_: None))
    monkeypatch.setattr(library_refresh, "invalidate_library",
                        lambda host: calls.append(("refresh", host is window)))
    app._bind_desktop_notice_work(NS(notice_work=runtime), window)
    app._start_notice_work_wakeup(
        window, timeout_add_seconds=lambda _, callback: scheduled.append(callback) or 1,
        source_remove=lambda _: None)
    pulse, = scheduled
    assert calls == []
    assert pulse() is True and calls == ["hint"]
    version[0] = 3
    # Ordinary task-finished hint never performs the refresh reentrantly.
    window._notice_work_tick()
    assert calls == ["hint", "hint"]
    assert pulse() is True
    assert calls[-2:] == ["hint", ("refresh", True)]
    assert pulse() is True and calls[-1] == "hint"
    assert calls.count(("refresh", True)) == 1
    version[0] = 4
    window._closed = True
    assert pulse() is False and calls.count(("refresh", True)) == 1
