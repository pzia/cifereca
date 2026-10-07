from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/task_runner.py"


def _deliver_source() -> str:
    tree = ast.parse(SOURCE.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TaskRunner")
    submit = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "submit")
    done = next(node for node in ast.walk(submit) if isinstance(node, ast.FunctionDef) and node.name == "done")
    deliver = next(node for node in ast.walk(done) if isinstance(node, ast.FunctionDef) and node.name == "deliver")
    return ast.unparse(deliver)


def test_closed_window_stops_widget_delivery_after_task_count_is_decremented():
    text = _deliver_source()

    decrement = text.index("self._count = max(0, self._count - 1)")
    closed_guard = text.index("getattr(self.window, '_closed', False)")
    task_finished = text.index("self.window._task_finished")

    assert decrement < closed_guard < task_finished


def test_detached_control_is_not_reenabled():
    text = _deliver_source()

    assert "control.get_root() is not None" in text
    assert "control.set_sensitive(True)" in text


def test_detached_control_does_not_block_success_callback_delivery():
    text = _deliver_source()

    detached_guard = text.index("control.get_root() is not None")
    success_callback = text.index("ok(payload)")
    closed_guard = text.index("getattr(self.window, '_closed', False)")

    assert closed_guard < detached_guard < success_callback
    assert "return False" not in text[detached_guard:success_callback]


def test_task_finished_is_delivered_before_success_callback():
    text = _deliver_source()

    assert text.index("self.window._task_finished") < text.index("ok(payload)")
