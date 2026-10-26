"""P1 #3134: one effective trace level in CLI, Core/UI and terminal mirror."""
from __future__ import annotations

import io
import logging
import sys
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca import app, logging_utils


def test_help_documents_trace_and_compatibility_without_gtk(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cifereca", "--help"])
    assert app.main() == 0
    help_text = capsys.readouterr().out
    assert all(flag in help_text for flag in ("--trace", "--debug", "--verbose"))
    assert "--no-plugins" in help_text


@pytest.mark.parametrize("flag", ["--trace", "--debug", "--verbose"])
def test_cli_override_reaches_core_before_plugin_initialization(monkeypatch, flag):
    gi = ModuleType("gi")
    gi.require_version = Mock()
    repo = ModuleType("gi.repository")

    class Application:
        def __init__(self, **_kwargs):
            pass

        def run(self, argv):
            assert argv == ["cifereca"]
            return 0

    repo.Adw = NS(Application=Application)
    repo.Gio = NS(Application=NS(do_shutdown=Mock()))
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    gui = ModuleType("cifereca.gui")
    gui.MainWindow = Mock()
    monkeypatch.setitem(sys.modules, "cifereca.gui", gui)

    diagnostic = NS(level="trace")
    core = NS(
        config=NS(), library=NS(), diagnostics=diagnostic,
        storage=NS(schema_version=lambda: 1), close=Mock(),
    )
    opened = Mock(return_value=core)
    configured = Mock(return_value=Mock())
    effective = Mock()
    monkeypatch.setattr(app.CiferecaCore, "open", opened)
    monkeypatch.setattr(app, "configure_logging", configured)
    monkeypatch.setattr(app, "set_diagnostics_level", effective)
    monkeypatch.setattr(sys, "argv", ["cifereca", flag])

    assert app.main() == 0
    configured.assert_called_once_with(verbose=True)
    opened.assert_called_once_with(
        no_plugins=False, force_plugins=(), activate_plugins=False,
        diagnostics_level="trace",
    )
    effective.assert_called_once_with("trace")


class _Terminal(io.StringIO):
    def isatty(self):
        return True


def test_runtime_trace_attaches_one_terminal_mirror_and_switches_live(monkeypatch, tmp_path):
    stream = _Terminal()
    logger = logging.getLogger("cifereca")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    previous_mode = logging_utils._diagnostic_level
    logger.handlers = []
    logger.propagate = False
    monkeypatch.setattr(logging_utils, "log_file", lambda: tmp_path / "app.log")
    monkeypatch.setattr(logging_utils.sys, "stderr", stream)
    try:
        logging_utils.configure_logging(verbose=False)
        assert len(logger.handlers) == 1
        logging_utils.set_diagnostics_level("trace")
        logging_utils.set_diagnostics_level("trace")
        assert len(logger.handlers) == 2
        logger.debug("TRACE runtime_hot_enabled")
        for handler in logger.handlers:
            handler.flush()
        assert "TRACE runtime_hot_enabled" in stream.getvalue()
        assert "TRACE runtime_hot_enabled" in (tmp_path / "app.log").read_text()

        logging_utils.set_diagnostics_level("normal")
        logger.info("TRACE runtime_hot_suppressed")
        logger.info("ordinary_normal_visible")
        for handler in logger.handlers:
            handler.flush()
        assert "TRACE runtime_hot_suppressed" not in stream.getvalue()
        assert "ordinary_normal_visible" in stream.getvalue()

        logging_utils.set_diagnostics_level("errors")
        logger.info("ordinary_info_suppressed")
        logger.error("ordinary_error_preserved")
        for handler in logger.handlers:
            handler.flush()
        text = (tmp_path / "app.log").read_text()
        assert "ordinary_info_suppressed" not in text
        assert "ordinary_error_preserved" in text
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = previous_handlers
        logger.level = previous_level
        logger.propagate = previous_propagate
        logging_utils._diagnostic_level = previous_mode
