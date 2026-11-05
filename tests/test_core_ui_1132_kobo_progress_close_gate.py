"""P0 #1132 — never write Kobo progress to detached GTK controls."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/sync_controller.py"


class Button:
    def __init__(self):
        self.label = "Sauvegarder"
        self.writes = []

    def get_label(self):
        return self.label

    def set_label(self, value):
        self.writes.append(value)
        self.label = value


def _method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name == name)
    queue = []
    ns = {
        "Gtk": NS(Button=Button),
        "TaskPriority": NS(USER_ACTION=1, ACTIVE_LOAN=2),
        "GLib": NS(
            idle_add=lambda callback: queue.append(callback),
            source_remove=lambda source: None,
        ),
    }
    code = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0),
              fn], type_ignores=[],
    ))
    exec(compile(code, str(SOURCE), "exec"), ns)
    return ns[name], queue


def test_closed_window_does_not_start_kobo_service_or_touch_control():
    run, queued = _method("run_kobo_backup")
    control = Button()
    assert run(NS(_closed=True), control=control) is None
    assert control.writes == []
    assert queued == []


def test_enqueued_progress_does_not_touch_button_after_close():
    run, queued = _method("run_kobo_backup")
    control = Button()
    tasks = []
    effects = []
    host = NS(
        _closed=False,
        service=NS(sync_kobo_backup=lambda *, progress: (
            progress({"event": "manifest", "total": 5}), {"downloaded": 0}
        )[1]),
        runner=NS(submit=lambda *args, **kwargs: tasks.append((args, kwargs))),
        _render_kobo_loans=lambda: effects.append("render"),
        _activity_result=lambda *args, **kwargs: effects.append("activity"),
        show_error=lambda *args: effects.append("error"),
        diagnostics=NS(add=lambda *args, **kwargs: effects.append("diagnostics")),
    )
    run(host, manual=True, control=control)
    assert len(tasks) == 1
    args, kwargs = tasks[0]
    assert kwargs["kind"] == "kobo-acquisition"
    assert kwargs["priority"] == 1
    args[1]()
    assert len(queued) == 1
    host._closed = True
    assert queued.pop(0)() is False
    assert control.writes == []
    args[2]({"downloaded": 1})
    args[3](RuntimeError("private provider token"))
    assert effects == []


def test_live_kobo_progress_and_summary_preserve_existing_path():
    run, queued = _method("run_kobo_backup")
    tasks = []
    effects = []
    control = Button()
    host = NS(
        _closed=False,
        service=NS(sync_kobo_backup=lambda *, progress: (
            progress({"event": "manifest", "total": 5}),
            {"downloaded": 1, "pending": 0, "unchanged": 0, "skipped": 0,
             "attention_required": 0, "errors": 0},
        )[1]),
        runner=NS(submit=lambda *args, **kwargs: tasks.append((args, kwargs))),
        _render_kobo_loans=lambda: effects.append("render"),
        _activity_result=lambda *args, **kwargs: effects.append("activity"),
    )
    run(host, manual=True, control=control)
    args, kwargs = tasks[0]
    summary = args[1]()
    assert len(queued) == 1
    assert queued[0]() is False
    assert control.writes == ["Synchronisation Kobo · manifest 5"]
    args[2](summary)
    assert control.label == "Sauvegarder"
    assert effects == ["render", "activity"]


def test_retired_legacy_timer_has_no_runtime_or_facade_entry_point():
    """#3198: scheduling is Core per source, not an inert GTK compatibility API.

    Old timer IDs are still cleared by window_close, covered separately.
    Keep manual Kobo acquisition and its close-gated callbacks tested above.
    """
    root = SOURCE.parent
    controller = ast.parse(SOURCE.read_text(encoding="utf-8"))
    facade = ast.parse((root / "main_window.py").read_text(encoding="utf-8"))
    functions = {
        node.name for node in controller.body if isinstance(node, ast.FunctionDef)
    }
    methods = {
        node.name
        for cls in facade.body if isinstance(cls, ast.ClassDef)
        for node in cls.body if isinstance(node, ast.FunctionDef)
    }
    assert "_configure_kobo_backup_timer" not in functions
    assert "_configure_kobo_backup_timer" not in methods
    assert "run_kobo_backup" in functions
    assert "run_kobo_backup" in methods
