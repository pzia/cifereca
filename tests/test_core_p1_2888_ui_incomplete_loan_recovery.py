"""#2888: explicit loan recovery must not announce success for Core business refusals.

Only MainWindow's existing callback is extracted; no GTK, network,
ADEPT, provider, loan rights or filesystem action is triggered.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cifereca.task_queue import TaskPriority


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def prepare_callback(window):
    module = ast.parse(MAIN.read_text(encoding="utf-8"))
    method = next(
        node for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "prepare_pnb_for_loan"
    )
    for param in method.args.args + method.args.kwonlyargs:
        param.annotation = None
    method.returns = None
    ns = {"TaskPriority": TaskPriority}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[method], type_ignores=[],
    )), str(MAIN), "exec"), ns)
    loan = SimpleNamespace(id="synthetic-loan")
    ns["prepare_pnb_for_loan"](window, loan)
    submit = window.runner.submit
    assert submit.call_count == 1
    assert submit.call_args.kwargs["kind"] == "loan-acquisition"
    return loan, submit.call_args.args[1], submit.call_args.args[2]


def window_with_outcome(outcome, *, reporter=True):
    window = SimpleNamespace(
        service=SimpleNamespace(reacquire_digital_loan=Mock(return_value=outcome)),
        runner=Mock(), refresh_library=Mock(), refresh_recent=Mock(),
        _render_dashboard=Mock(), loan_page=SimpleNamespace(
            detail=SimpleNamespace(show_loan=Mock()),
        ), toast=Mock(), show_error=Mock(),
    )
    if reporter:
        window._activity_result = Mock()
    return window


@pytest.mark.parametrize(("outcome", "expected"), [
    ({"status": "attention_required", "code": "adept_identity_unproven"},
     "Intervention requise pour récupérer le prêt"),
    ({"status": "retryable_error", "code": "reacquisition_failed"},
     "Récupération du prêt à réessayer"),
    ({"status": "failed", "code": "unclassified_error"},
     "Récupération du prêt non confirmée"),
    ({}, "Récupération du prêt non confirmée"),
    (None, "Récupération du prêt non confirmée"),
])
def test_incomplete_business_outcome_is_not_reported_as_success(outcome, expected):
    window = window_with_outcome(outcome)
    loan, work, completed = prepare_callback(window)
    assert work() is outcome
    completed(outcome)
    window.service.reacquire_digital_loan.assert_called_once_with(loan)
    window.refresh_library.assert_called_once_with(False)
    window.refresh_recent.assert_called_once_with()
    window.loan_page.detail.show_loan.assert_called_once_with(loan)
    window.toast.assert_not_called()
    window.show_error.assert_not_called()
    window._activity_result.assert_called_once()
    args, kwargs = window._activity_result.call_args
    assert kwargs == {"success": False}
    assert expected in args[0]
    assert "Livre récupéré" not in args[0]
    if isinstance(outcome, dict) and outcome.get("code"):
        assert outcome["code"] in args[0]


def test_unknown_provider_code_never_echoes_potential_credentials():
    result = {
        "status": "attention_required",
        "code": "session-token=secret-authentication-value",
    }
    window = window_with_outcome(result)
    _loan, work, completed = prepare_callback(window)
    completed(work())
    text = window._activity_result.call_args.args[0]
    assert "Intervention requise" in text
    assert "session-token" not in text
    assert "secret-authentication-value" not in text


def test_incomplete_outcome_without_activity_reporter_opens_failure_not_success_toast():
    result = {"status": "retryable_error", "code": "acquisition_busy"}
    window = window_with_outcome(result, reporter=False)
    _loan, work, completed = prepare_callback(window)
    completed(work())
    window.show_error.assert_called_once()
    message, heading = window.show_error.call_args.args
    assert "acquisition_busy" in message
    assert heading == "Téléchargement non terminé"
    window.toast.assert_not_called()


def test_complete_loan_still_announces_success_without_false_failure():
    result = {"status": "complete", "adept_return_available": True}
    window = window_with_outcome(result)
    _loan, work, completed = prepare_callback(window)
    completed(work())
    window._activity_result.assert_called_once()
    args, kwargs = window._activity_result.call_args
    assert kwargs == {}
    assert "Livre récupéré" in args[0]
    assert "restitution ADEPT disponible" in args[0]
    window.show_error.assert_not_called()
