from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _main_window_method(name: str):
    tree = ast.parse(SOURCE.read_text())
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    method = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {
        "TaskPriority": SimpleNamespace(USER_ACTION="user-action"),
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[name]


class _Runner:
    def __init__(self):
        self.calls = []

    def submit(self, label, work, ok, error, **kwargs):
        self.calls.append({
            "label": label,
            "work": work,
            "ok": ok,
            "error": error,
            **kwargs,
        })


def test_reconcile_from_detail_is_dispatched_through_runner_worker():
    calls = []
    service = SimpleNamespace(
        reconcile_adept_acquisition=lambda acquisition_id: calls.append(acquisition_id) or {"status": "resolved"}
    )
    runner = _Runner()
    detail = SimpleNamespace(show_loan=lambda loan: None)
    host = SimpleNamespace(
        service=service,
        runner=runner,
        loan_page=SimpleNamespace(detail=detail),
        toast=lambda *args: None,
        show_error=lambda *args: None,
    )
    loan = SimpleNamespace()
    control = object()

    method = _main_window_method("reconcile_adept_from_detail")
    method(host, "acq-1", loan, control)

    assert calls == []
    assert len(runner.calls) == 1
    submitted = runner.calls[0]
    assert submitted["kind"] == "adept-inventory"
    assert submitted["control"] is control

    result = submitted["work"]()
    assert calls == ["acq-1"]
    assert result["status"] == "resolved"


def test_obsolete_return_detail_action_not_exported_by_mainwindow():
    """The read-only ADEPT reconciliation action remains available."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body
               if isinstance(x, ast.ClassDef) and x.name == "MainWindow")
    methods = {x.name for x in cls.body if isinstance(x, ast.FunctionDef)}
    assert "return_adept_from_detail" not in methods
    assert "reconcile_adept_from_detail" in methods
    assert 'kind="adept-return"' not in SOURCE.read_text(encoding="utf-8")
