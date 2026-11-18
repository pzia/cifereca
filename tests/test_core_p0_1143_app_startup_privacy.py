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
        assert read_consolidated_notice_cover(core.storage, old.document).sources[0].public_bibliography.title == "Nouveau"
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []
        win._closed = True
        assert win._notice_work_tick() is None
    win._closed = False
    assert win._notice_work_tick() is None
