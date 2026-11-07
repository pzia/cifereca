"""#3389: installed GUI --perf starts the single bounded monitor before GTK/Core."""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest

from cifereca import app
from cifereca.gui.ui_perf import UiPerfMonitor
from cifereca.gui import startup_coordinator


ROOT = Path(__file__).resolve().parents[1]


def _fake_gui_run(monkeypatch, arguments):
    """Use the real app entry/argument handling, with an isolated GTK host.

    GTK and provider objects are deliberately fake; real GTK smoke belongs
    to the existing Xvfb E2E runner, not a second in-process test harness.
    """
    observed = NS(
        windows=[], app_argv=[], core_options=[], logging_verbose=[],
        core_closed=0, queued=[],
    )

    class Application:
        def __init__(self, *, application_id):
            assert application_id == "org.cifereca.Cifereca"

        def run(self, argv):
            observed.app_argv.append(list(argv))
            self.do_activate()
            while observed.queued:
                callback = observed.queued.pop(0)
                callback()
            self.do_shutdown()
            return 0

    class Window:
        def __init__(self, app_instance, config, service, diagnostics, *, ui_perf=None):
            assert isinstance(app_instance, Application)
            self.ui_perf = ui_perf if ui_perf is not None else UiPerfMonitor()
            observed.windows.append({
                "monitor": self.ui_perf,
                "active_before_shell": self.ui_perf.detailed_capture_enabled,
                "report_before_shell": self.ui_perf.performance_report(),
            })
            # A rendering phase can be observed only if opt-in was already on.
            if self.ui_perf.detailed_capture_enabled:
                self.ui_perf.record_phase(
                    "library", "render", 1.5, trigger="navigation",
                )

        def present(self):
            pass

        def activate_startup_plugins(self):
            self.ui_perf.record_phase(
                "plugin", "worker", 2.5, trigger="automatic",
            )
            return False

    gi = ModuleType("gi")
    gi.require_version = lambda *_: None
    repository = ModuleType("gi.repository")
    repository.Adw = NS(Application=Application)
    repository.Gio = NS(Application=NS(do_shutdown=lambda *_: None))
    repository.GLib = NS(idle_add=lambda callback: observed.queued.append(callback))
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    from cifereca import gui
    monkeypatch.setitem(gui.__dict__, "MainWindow", Window)
    core = NS(
        config=object(), library=object(), diagnostics=NS(level="normal"),
        storage=NS(schema_version=lambda: 1),
        close=lambda: setattr(observed, "core_closed", observed.core_closed + 1),
    )
    monkeypatch.setattr(
        app.CiferecaCore, "open",
        lambda **kwargs: (observed.core_options.append(kwargs), core)[1],
    )
    monkeypatch.setattr(
        app, "configure_logging",
        lambda *, verbose=False: (
            observed.logging_verbose.append(verbose),
            logging.getLogger("cifereca")
        )[1],
    )
    monkeypatch.setattr(
        app, "build_identity",
        lambda: {"status": "unavailable", "commit_sha": None},
    )
    monkeypatch.setattr(app, "set_diagnostics_level", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["cifereca", *arguments])
    assert app.main() == 0
    assert observed.core_closed == 1
    assert len(observed.windows) == 1
    return observed


@pytest.mark.parametrize("extra,expected_verbose", [
    ([], False),
    (["--trace"], True),
    (["--no-plugins"], False),
])
def test_perf_is_early_single_instance_with_unrelated_trace_off_by_default(
    monkeypatch, extra, expected_verbose,
):
    observed = _fake_gui_run(monkeypatch, ["--perf", *extra])
    window = observed.windows[0]
    mon = window["monitor"]
    assert window["active_before_shell"] is True
    assert mon.detailed_capture_enabled is True
    assert mon.capture_generation == 1
    assert observed.logging_verbose == [expected_verbose]
    assert ("diagnostics_level" in observed.core_options[0]) is expected_verbose
    assert observed.app_argv == [["cifereca"]]
    assert window["report_before_shell"]["schema"] == "cifereca.performance.phases/1"
    assert {"startup.gtk_import", "startup.core_open"} <= set(
        window["report_before_shell"]["stats"]
    )
    assert [row["phase"] for row in window["report_before_shell"]["events"]] == [
        "gtk_import", "core_open",
    ]
    assert {row["category"] for row in window["report_before_shell"]["events"]} == {"startup"}
    report = mon.performance_report()
    assert "library.render" in report["stats"] and "plugin.worker" in report["stats"]
    assert report["resource_window"]["state"] == "active"
    assert report["remote_upload"] is False
    dumped = json.dumps(report, allow_nan=False)
    assert "http_trace" not in dumped and "password" not in dumped


def test_without_perf_is_passive_even_after_a_previous_opted_in_run(monkeypatch):
    first = _fake_gui_run(monkeypatch, ["--perf"])
    assert first.windows[0]["monitor"].detailed_capture_enabled
    second = _fake_gui_run(monkeypatch, [])
    window = second.windows[0]
    assert window["monitor"] is not first.windows[0]["monitor"]
    assert not window["active_before_shell"]
    assert window["report_before_shell"]["events"] == []
    assert window["report_before_shell"]["no_samples"] is True
    assert second.app_argv == [["cifereca"]]
    assert second.logging_verbose == [False]


def test_real_gui_entry_help_exposes_perf_without_importing_gtk(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cifereca", "--help"])
    assert app.main() == 0
    output = capsys.readouterr()
    assert "--perf" in output.out and "--trace" in output.out
    assert output.err == ""


def test_perf_initialization_failure_is_explicit_and_fails_before_gui(
    monkeypatch, capsys,
):
    monkeypatch.setattr(sys, "argv", ["cifereca", "--perf"])
    monkeypatch.setattr(app, "configure_logging", lambda **kw: logging.getLogger("cifereca"))
    monkeypatch.setattr(
        app.CiferecaCore, "open",
        lambda **kw: pytest.fail("Core opened after performance monitor failure"),
    )
    monkeypatch.setattr(
        UiPerfMonitor, "enable_detailed_capture",
        lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic secret")),
    )
    assert app.main() == 2
    output = capsys.readouterr()
    assert output.err.strip() == "performance_capture_unavailable"
    assert "synthetic secret" not in output.err


def test_real_window_wiring_uses_one_monitor_before_shell_and_settings():
    tree = ast.parse((ROOT / "src/cifereca/gui/main_window.py").read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "MainWindow")
    init = next(node for node in owner.body if isinstance(node, ast.FunctionDef)
                and node.name == "__init__")
    source = ast.unparse(init)
    assert "self.ui_perf = ui_perf if ui_perf is not None else UiPerfMonitor()" in source
    assert source.index("self.ui_perf = ui_perf") < source.index("self.main_shell = self._build_main_shell()")
    assert source.index("self.ui_perf.record_phase(") > source.index(
        "self.ui_perf.record('startup.shell_interactive', shell_ms)"
    )


def test_plugin_activation_phase_is_produced_by_real_startup_coordinator():
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    updates = []
    window = NS(
        _closed=False, ui_perf=monitor, plugin_manager=NS(
            activate=lambda: updates.append("activated"),
        ), service=NS(attach_plugin_manager=lambda *_: updates.append("attached")),
        _plugins_changed=lambda: updates.append("refreshed"),
        _connect_sources_if_open=lambda: None,
        diagnostics=NS(add=lambda *_a, **_kw: None),
    )
    assert startup_coordinator.activate_startup_plugins(
        window, idle_add=lambda callback: updates.append(callback),
    ) is False
    assert updates[:3] == ["activated", "attached", "refreshed"]
    assert "startup.plugins_activation" in monitor.performance_report()["stats"]
    assert "plugins_activation" in [
        row["phase"] for row in monitor.performance_report()["events"]
    ]


def test_stop_then_restart_keeps_startup_aggregates_and_single_monitor():
    mon = UiPerfMonitor()
    mon.enable_detailed_capture(True)
    mon.record_phase("startup", "gtk_import", 10, trigger="automatic")
    mon.record_phase("startup", "core_open", 20, trigger="automatic")
    before = mon.performance_report()
    mon.enable_detailed_capture(False)
    stopped = mon.performance_report()
    assert stopped["events"] == before["events"]
    assert stopped["resource_window"]["state"] == "stopped"
    mon.enable_detailed_capture(True)
    resumed = mon.performance_report()
    assert resumed["stats"]["startup.gtk_import"]["count"] == 1
    assert resumed["stats"]["startup.core_open"]["count"] == 1
    assert mon.capture_generation == 3
    # Session startup observations stay in the bounded timeline, with their
    # original epoch; restarted user-capture samples get the new epoch.
    assert [event["phase"] for event in resumed["events"]] == [
        "gtk_import", "core_open",
    ]
    assert {event["capture_generation"] for event in resumed["events"]} == {1}
    mon.record_phase("library", "render", 7)
    report = mon.performance_report()
    assert report["stats"]["startup.core_open"]["count"] == 1
    assert [event["category"] for event in report["events"]] == [
        "startup", "startup", "library",
    ]
    assert report["events"][-1]["capture_generation"] == 3


def test_many_followup_events_do_not_erase_startup_evidence():
    mon = UiPerfMonitor(max_events=8)
    mon.enable_detailed_capture(True)
    for phase in ("gtk_import", "core_open", "shell_interactive"):
        assert mon.record_phase("startup", phase, 1)
    for index in range(200):
        assert mon.record_phase("task", "worker", index)
    report = mon.performance_report()
    assert len(report["events"]) == 8
    assert [row["phase"] for row in report["events"][:3]] == [
        "gtk_import", "core_open", "shell_interactive",
    ]
    assert report["events_dropped"] == 195
    mon.enable_detailed_capture(False)
    mon.enable_detailed_capture(True)
    assert [row["phase"] for row in mon.performance_report()["events"]] == [
        "gtk_import", "core_open", "shell_interactive",
    ]
