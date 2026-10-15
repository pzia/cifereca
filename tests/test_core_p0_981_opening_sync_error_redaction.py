"""#981: opening sync failure must never echo arbitrary provider exceptions."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def _opening_sync_error_handler():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/sync_controller.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    opening = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_opening_sync"
    )
    err = next(
        node for node in opening.body
        if isinstance(node, ast.FunctionDef) and node.name == "err"
    )
    # Compile exactly the actual callback body, without GTK imports or workers.
    err.decorator_list = []
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[err], type_ignores=[])),
                 str(path), "exec"), namespace)
    return namespace["err"]


@pytest.mark.parametrize("secret", [
    "https://provider.invalid/loan?bearer=PRIVATE_TOKEN",
    "/home/reader/.config/cifereca/private.json",
    "account_id=PRIVATE_ACCOUNT",
])
@pytest.mark.parametrize("manual", [False, True])
def test_opening_sync_error_redacts_diagnostics_and_user_feedback(secret, manual):
    callback = _opening_sync_error_handler()
    diagnostics = []
    errors = []
    dashboards = []
    host = SimpleNamespace(
        _closed=False,
        diagnostics=SimpleNamespace(add=lambda *args, **kw: diagnostics.append((args, kw))),
        _render_dashboard=lambda: dashboards.append(True),
        show_error=lambda *args: errors.append(args),
    )
    callback.__globals__["self"] = host
    callback.__globals__["control"] = object() if manual else None
    callback(RuntimeError(secret))
    assert len(diagnostics) == 1
    assert diagnostics[0][1]["detail"] == {"reason": "opening_sync_failed"}
    assert dashboards == [True]
    assert len(errors) == int(manual)
    assert secret not in repr(diagnostics) + repr(errors)


def test_closed_window_does_not_report_provider_failure():
    callback = _opening_sync_error_handler()
    host = SimpleNamespace(_closed=True)
    callback.__globals__["self"] = host
    callback.__globals__["control"] = object()
    callback(RuntimeError("PRIVATE_TOKEN"))
