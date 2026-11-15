"""P0 #5975/#5833: a finished loan reacquisition must not repaint another GTK account.

The real MainWindow method is AST-compiled without importing GTK, network,
ADEPT or provider services. The worker retains its explicit Loan argument.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.models import Loan
from cifereca.task_queue import TaskPriority


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def method():
    owner = next(
        node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    fn = next(
        node for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "prepare_pnb_for_loan"
    )
    for param in fn.args.args + fn.args.kwonlyargs:
        param.annotation = None
    fn.returns = None
    namespace = {"TaskPriority": TaskPriority}
    exec(compile(
        ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
        str(SOURCE), "exec",
    ), namespace)
    return namespace["prepare_pnb_for_loan"]


def loan(identity="alice"):
    return Loan(
        id="loan-exact", provider="PNB", medium="digital", record_id="edition",
        title="Private test loan", identity_id=identity, source_provider="paris",
        source_credential="cred-a", source_account_id="borrower-a",
    )


def rig(*, owner="alice", loan_identity="alice"):
    source = loan(loan_identity)
    service = NS(
        current_identity=NS(identity_id=owner),
        _account_context_epoch=7,
        reacquire_digital_loan=Mock(return_value={"status": "complete"}),
    )
    window = NS(
        service=service, _identity_context_generation=11, _closed=False,
        runner=Mock(), refresh_library=Mock(), refresh_recent=Mock(),
        _render_dashboard=Mock(), loan_page=NS(detail=NS(show_loan=Mock())),
        _activity_result=Mock(), toast=Mock(), show_error=Mock(),
    )
    method()(window, source)
    assert window.runner.submit.call_count == 1
    _label, work, ok, failed = window.runner.submit.call_args.args
    assert window.runner.submit.call_args.kwargs["kind"] == "loan-acquisition"
    return window, source, work, ok, failed


def assert_no_gtk_delivery(w):
    w.refresh_library.assert_not_called()
    w.refresh_recent.assert_not_called()
    w._render_dashboard.assert_not_called()
    w.loan_page.detail.show_loan.assert_not_called()
    w._activity_result.assert_not_called()
    w.toast.assert_not_called()
    w.show_error.assert_not_called()


def test_switch_to_b_after_enqueue_keeps_exact_loan_but_no_gtk_delivery():
    w, source, work, ok, failed = rig()
    w.service.current_identity = NS(identity_id="bob")
    assert work() == {"status": "complete"}
    w.service.reacquire_digital_loan.assert_called_once_with(source)
    ok({"status": "complete"})
    failed(RuntimeError("https://private.invalid/?token=SECRET"))
    assert_no_gtk_delivery(w)


def test_a_b_a_different_generation_never_repaints_old_loan():
    w, source, work, ok, failed = rig()
    w.service.current_identity = NS(identity_id="bob")
    w._identity_context_generation += 1
    w.service.current_identity = NS(identity_id="alice")
    w._identity_context_generation += 1
    work()
    ok({"status": "complete"})
    assert_no_gtk_delivery(w)


@pytest.mark.parametrize("change", [
    "account_epoch", "identity_generation", "window_closed", "wrong_source_owner",
])
def test_same_identity_is_not_enough_for_stale_or_wrong_owner_completion(change):
    w, _, work, ok, failed = rig(
        loan_identity="bob" if change == "wrong_source_owner" else "alice",
    )
    if change == "account_epoch":
        w.service._account_context_epoch += 1
    elif change == "identity_generation":
        w._identity_context_generation += 1
    elif change == "window_closed":
        w._closed = True
    work()
    ok({"status": "complete"})
    failed(RuntimeError("PRIVATE-ERROR"))
    assert_no_gtk_delivery(w)


class HostileText(str):
    def __eq__(self, _other):
        raise AssertionError("plugin-controlled equality")


def test_hostile_identity_in_gtk_selector_never_runs_custom_equality():
    w, source, work, ok, failed = rig()
    w.service.current_identity = NS(identity_id=HostileText("alice"))
    work()
    ok({"status": "complete"})
    failed(RuntimeError("PRIVATE-ERROR"))
    assert_no_gtk_delivery(w)


def test_current_scope_keeps_success_and_exact_loan():
    w, source, work, ok, _failed = rig()
    assert work() == {"status": "complete"}
    ok({"status": "complete"})
    w.service.reacquire_digital_loan.assert_called_once_with(source)
    w.refresh_library.assert_called_once_with(False)
    w.refresh_recent.assert_called_once_with()
    w._render_dashboard.assert_called_once_with()
    w.loan_page.detail.show_loan.assert_called_once_with(source)
    assert w._activity_result.call_args.args == ("Livre récupéré",)


def test_current_scope_redacts_private_provider_exception():
    w, _, work, ok, failed = rig()
    failed(RuntimeError("https://private.invalid/?token=SECRET"))
    w.show_error.assert_called_once_with(
        "Acquisition indisponible", "Acquisition impossible",
    )
    assert "SECRET" not in repr(w.show_error.call_args)
    w.refresh_library.assert_not_called()
