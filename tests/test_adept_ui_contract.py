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


def test_unconfirmed_return_never_announces_confirmation():
    runner = _Runner()
    toasts = []
    host = SimpleNamespace(
        runner=runner,
        service=SimpleNamespace(return_adept_acquisition=lambda acquisition_id: {"verification": "unconfirmed"}),
        toast=lambda message, duration: toasts.append(message),
        loan_page=SimpleNamespace(detail=SimpleNamespace(show_loan=lambda loan: None)),
        refresh_library=lambda *args: None,
        refresh_loans=lambda *args: None,
        show_error=lambda *args: None,
        _displayed_loan=None,
    )

    method = _main_window_method("return_adept_from_detail")
    method(host, "acq-1", object())

    assert len(runner.calls) == 1
    submitted = runner.calls[0]
    assert submitted["kind"] == "adept-return"

    submitted["ok"]({"verification": "unconfirmed"})

    assert len(toasts) == 1
    message = toasts[0]
    assert "retour non confirmé" in message
    assert "Retour enregistré" not in message
    assert "état local vérifié" not in message
