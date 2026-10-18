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
