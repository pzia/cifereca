"""#1358: cancellable, async maintenance preview without a GTK display.

The production _refresh_tools_page body is evaluated with inert GTK widgets.
Worker jobs are held deliberately so cancellation may race a slow plan.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import maintenance_tools as guard
from cifereca.gui.library_inspection_view import LocalInspectionController
from cifereca.gui.maintenance_result_view import MaintenanceResultView
from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


class Button:
    def __init__(self, label=""):
        self.label = label
        self.visible = True
        self.sensitive = True
        self.handlers = {}
        self.root = object()

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def click(self):
        self.handlers["clicked"](self)

    def set_visible(self, value):
        self.visible = value

    def set_sensitive(self, value):
        self.sensitive = value

    def get_root(self):
        return self.root


class Container:
    def __init__(self):
        self.children = []

    def append(self, value):
        self.children.append(value)

    def add(self, value):
        self.children.append(value)

    def add_suffix(self, value):
        self.children.append(value)

    def set_title(self, title):
        self.title = title

    def set_subtitle(self, subtitle):
        self.subtitle = subtitle

    # Inert GTK methods used by the scrollable preview's label and viewport.
    def set_wrap(self, value): self.wrap = value
    def set_wrap_mode(self, value): self.wrap_mode = value
    def set_xalign(self, value): self.xalign = value
    def set_yalign(self, value): self.yalign = value
    def set_selectable(self, value): self.selectable = value
    def set_max_width_chars(self, value): self.max_width_chars = value
    def set_focusable(self, value): self.focusable = value
    def set_policy(self, horizontal, vertical): self.policy = (horizontal, vertical)
    def set_min_content_height(self, value): self.min_height = value
    def set_max_content_height(self, value): self.max_height = value
    def set_propagate_natural_height(self, value): self.propagate_height = value
    def set_child(self, child): self.children = [child]


class Dialog:
    shown = []

    def __init__(self):
        self.handlers = {}
        self.responses = {}

    @classmethod
    def new(cls, *_args):
        instance = cls()
        cls.shown.append(instance)
        return instance

    def add_response(self, key, label):
        self.responses[key] = label

    def set_response_appearance(self, *_args):
        pass

    def set_extra_child(self, child):
        self.extra_child = child

    def connect(self, key, callback):
        self.handlers[key] = callback

    def present(self):
        self.presented = True


class Tool:
    def __init__(self):
        self.plans = 0
        self.runs = 0
        self.plan_result = NS(
            tool_id="library.duplicates.cleanup", summary="Sans suppression",
            changes=(), warnings=(), facts={"candidates": []},
        )

    def describe(self):
        return NS(
            title="Nettoyer les doublons", description="Audit", icon_name="edit-delete",
            scope="library", danger_level="destructive",
            supports_dry_run=True, cancellable=True,
        )

    def plan(self, _context):
        self.plans += 1
        return self.plan_result

    def run(self, _plan, _control):
        self.runs += 1
        return NS(ok=True, summary="Terminé")


def _method():
    module = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in module.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "_refresh_tools_page")
    dispose = next(n for n in cls.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_dispose_maintenance_lines")
    code = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__",
                       names=[ast.alias(name="annotations")], level=0), dispose, fn,
    ], type_ignores=[]))
    # Historically this fixture only mounted tools whose IDs were known
    # to the progress registry. All maintenance.tool rows now get a safe
    # status line, so the inert GTK harness must supply that existing
    # presentation contract too (without starting a real GTK main loop).
    def build_test_line(*, Gtk, idle_add, alive, activity_label):
        feedback = MaintenanceLineProgress(
            idle_add=lambda callback: callback(),
            render=lambda state: None, alive=alive,
            activity_label=activity_label,
        )
        return Container(), feedback

    namespace = {
        "build_maintenance_line": build_test_line,
        "MaintenanceResultView": MaintenanceResultView,
        "LocalInspectionController": LocalInspectionController,
        "clear_box": lambda box: box.children.clear(),
        "Gtk": NS(Button=Button, Label=lambda **_kw: Container(),
                  ScrolledWindow=Container,
                  PolicyType=NS(NEVER=0, AUTOMATIC=1)),
        "Pango": NS(WrapMode=NS(WORD_CHAR=1)),
        "Adw": NS(
            PreferencesGroup=Container, ActionRow=Container,
            MessageDialog=Dialog,
            ResponseAppearance=NS(DESTRUCTIVE=1, SUGGESTED=2),
        ),
        "MaintenanceContext": lambda **kw: NS(**kw),
        "MaintenanceCancelControl": guard.MaintenanceCancelControl,
        "MaintenanceCancelledError": guard.MaintenanceCancelledError,
        "MaintenanceBusyError": guard.MaintenanceBusyError,
        "MaintenancePlanChangedError": guard.MaintenancePlanChangedError,
        "BUSY_MESSAGE": guard.BUSY_MESSAGE,
        "CHANGED_MESSAGE": guard.CHANGED_MESSAGE,
        "preview_maintenance_tool": guard.preview_maintenance_tool,
        "execute_maintenance_tool": guard.execute_maintenance_tool,
        "TaskPriority": NS(MAINTENANCE=1),
        "GLib": NS(idle_add=lambda callback: callback()),
    }
    exec(compile(code, str(MAIN), "exec"), namespace)
    def refresh(host):
        host._dispose_maintenance_lines = lambda: namespace["_dispose_maintenance_lines"](host)
        return namespace["_refresh_tools_page"](host)
    # Keep the production namespace injectable by specialised headless fixtures.
    refresh.production_globals = namespace
    return refresh


def _mounted():
    Dialog.shown.clear()
    tool = Tool()
    jobs = []
    errors = []
    host = NS(
        _closed=False, _tools_box=Container(),
        plugin_manager=NS(services=lambda _name: [
            NS(service=tool, plugin_id="maintenance.library.audit"),
        ]),
        service=NS(current_identity=NS(identity_id="alice", label="Alice")),
        action_button=lambda title, _icon: Button(title),
        runner=NS(submit=lambda *args, **kwargs: jobs.append((args, kwargs))),
        show_error=lambda *args: errors.append(args),
    )
    _method()(host)
    row = host._tools_box.children[0].children[0]
    preview, cancel, status = row.children
    assert status is not None  # future tools have a safe generic progress line
    return host, tool, jobs, errors, preview, cancel


def test_preview_cancel_before_worker_does_not_scan_or_open_dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "lock")
    _host, tool, jobs, errors, preview, cancel = _mounted()
    preview.click()
    assert len(jobs) == 1 and jobs[0][1]["kind"] == "maintenance-plan"
    assert tool.plans == 0
    assert cancel.visible and cancel.sensitive
    cancel.click()
    assert not cancel.sensitive
    args, _kwargs = jobs[0]
    with pytest.raises(guard.MaintenanceCancelledError):
        args[1]()
    args[3](guard.MaintenanceCancelledError("cancelled"))
    assert errors == []
    assert Dialog.shown == []
    assert tool.plans == 0


def test_preview_cancel_after_slow_worker_drops_stale_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "lock")
    _host, tool, jobs, errors, preview, cancel = _mounted()
    preview.click()
    args, _kwargs = jobs[0]
    plan = args[1]()
    assert tool.plans == 1
    cancel.click()
    args[2](plan)
    assert Dialog.shown == []
    assert errors == []
    assert tool.runs == 0
    assert not cancel.visible


def test_preview_navigation_detach_does_not_open_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "lock")
    _host, tool, jobs, _errors, preview, cancel = _mounted()
    preview.click()
    args, _kwargs = jobs[0]
    plan = args[1]()
    preview.root = None
    args[2](plan)
    assert Dialog.shown == []
    assert tool.runs == 0


def test_uncancelled_preview_requires_second_explicit_run(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "lock")
    _host, tool, jobs, _errors, preview, cancel = _mounted()
    preview.click()
    args, _kwargs = jobs[0]
    plan = args[1]()
    args[2](plan)
    assert not cancel.visible
    assert len(Dialog.shown) == 1
    assert tool.runs == 0
    Dialog.shown[0].handlers["response"](Dialog.shown[0], "cancel")
    assert tool.runs == 0
    assert len(jobs) == 1
