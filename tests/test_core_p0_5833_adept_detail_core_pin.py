"""P0 #5833: queued ADEPT detail and protected loan operations keep Core A.

Real MainWindow method bodies, isolated from GTK/provider/DRM/SQLite I/O.
Only the user-admitted Core may execute; GTK may render solely in its original
context, even when a replacement Core has the same visible borrower label.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.models import Loan
from cifereca.task_queue import TaskPriority


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


@lru_cache(maxsize=None)
def _method(name):
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(
        x for x in module.body if isinstance(x, ast.ClassDef)
        and x.name == "MainWindow"
    )
    method = next(
        x for x in owner.body if isinstance(x, ast.FunctionDef)
        and x.name == name
    )
    prelude = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0,
    )
    scope = {"TaskPriority": TaskPriority, "Loan": Loan}
    exec(compile(
        ast.fix_missing_locations(ast.Module(
            body=[prelude, method], type_ignores=[],
        )), str(SOURCE), "exec",
    ), scope)
    return scope[name]


def _loan():
    return Loan(
        id="loan-a", provider="PNB", medium="digital", record_id="edition-a",
        title="Synthetic book", identity_id="alice", source_provider="paris",
        source_credential="credential-a", source_account_id="borrower-a",
    )


def _host():
    original = NS(
        current_identity=NS(identity_id="alice"), _account_context_epoch=9,
        adept_loan_choices=Mock(return_value=[{"id": "adept-a"}]),
        reconcile_adept_acquisition=Mock(return_value={"status": "resolved"}),
        reacquire_digital_loan=Mock(return_value={"status": "complete"}),
    )
    requests = []
    host = NS(
        service=original, _closed=False, _identity_context_generation=3,
        _account_context_generation=5,
        runner=NS(submit=lambda *args, **kw: requests.append((args, kw))),
        _present_adept_loan_choices=Mock(),
        loan_page=NS(detail=NS(show_loan=Mock())),
        _activity_result=Mock(), show_error=Mock(), toast=Mock(),
        refresh_library=Mock(), refresh_recent=Mock(), _render_dashboard=Mock(),
    )
    return host, original, requests


def _replace_core(host):
    other = NS(
        current_identity=NS(identity_id="alice"), _account_context_epoch=9,
        adept_loan_choices=Mock(side_effect=AssertionError("foreign ADEPT read")),
        reconcile_adept_acquisition=Mock(
            side_effect=AssertionError("foreign ADEPT mutation")
        ),
        reacquire_digital_loan=Mock(
            side_effect=AssertionError("foreign loan acquisition")
        ),
    )
    host.service = other
    return other


def _no_gtk(host):
    host._present_adept_loan_choices.assert_not_called()
    host.loan_page.detail.show_loan.assert_not_called()
    host._activity_result.assert_not_called()
    host.show_error.assert_not_called()
    host.refresh_library.assert_not_called()
    host.refresh_recent.assert_not_called()
    host.toast.assert_not_called()


def test_queued_adept_choices_use_original_core_even_when_window_rebound():
    host, original, requests = _host()
    book = _loan()
    _method("choose_adept_loan")(host, "acquisition-a", book, object())
    (label, work, done, failed), options = requests[-1]
    assert label.startswith("Lecture des prêts ADEPT")
    assert options["kind"] == "adept-inventory"
    foreign = _replace_core(host)
    assert work() == [{"id": "adept-a"}]
    original.adept_loan_choices.assert_called_once_with("acquisition-a")
    foreign.adept_loan_choices.assert_not_called()
    done([{"id": "adept-a"}])
    failed(RuntimeError("PRIVATE-ADOBE-TOKEN"))
    _no_gtk(host)


def test_queued_adept_reconciliation_never_mutates_foreign_core():
    host, original, requests = _host()
    book = _loan()
    _method("reconcile_adept_from_detail")(host, "acquisition-a", book, object())
    (label, work, done, failed), options = requests[-1]
    assert options["kind"] == "adept-inventory"
    foreign = _replace_core(host)
    assert work() == {"status": "resolved"}
    original.reconcile_adept_acquisition.assert_called_once_with("acquisition-a")
    foreign.reconcile_adept_acquisition.assert_not_called()
    done({"status": "resolved"})
    failed(RuntimeError("PRIVATE-ADOBE-TOKEN"))
    _no_gtk(host)


def test_reacquisition_uses_click_bound_core_without_foreign_gtk_receipt():
    host, original, requests = _host()
    book = _loan()
    _method("prepare_pnb_for_loan")(host, book)
    (label, work, done, failed), options = requests[-1]
    assert options["kind"] == "loan-acquisition"
    foreign = _replace_core(host)
    assert work() == {"status": "complete"}
    original.reacquire_digital_loan.assert_called_once_with(book)
    foreign.reacquire_digital_loan.assert_not_called()
    done({"status": "complete"})
    failed(RuntimeError("PRIVATE-LOAN-TOKEN"))
    _no_gtk(host)


@pytest.mark.parametrize("field,replacement", [
    ("id", "loan-b"), ("identity_id", "bob"),
    ("source_provider", "foreign-provider"),
    ("source_credential", "credential-b"),
    ("source_account_id", "borrower-b"),
    ("record_id", "edition-b"),
])
def test_queued_reacquisition_cannot_change_source_before_worker(field, replacement):
    host, original, requests = _host()
    book = _loan()
    _method("prepare_pnb_for_loan")(host, book)
    work = requests[-1][0][1]
    setattr(book, field, replacement)
    with pytest.raises(ValueError, match="loan_reacquisition_source_changed"):
        work()
    original.reacquire_digital_loan.assert_not_called()


@pytest.mark.parametrize("name", ["choose_adept_loan", "reconcile_adept_from_detail"])
def test_adept_detail_aba_does_not_repaint_older_result(name):
    host, original, requests = _host()
    book = _loan()
    _method(name)(host, "acquisition-a", book, object())
    (_label, work, done, failed), _ = requests[-1]
    result = work()
    # A -> B -> A can return to the exact same native Core and label.
    original.current_identity = NS(identity_id="bob")
    host._account_context_generation += 1
    original.current_identity = NS(identity_id="alice")
    host._account_context_generation += 1
    done(result)
    failed(RuntimeError("PRIVATE-ADEPT"))
    _no_gtk(host)


class HostileIdentity(str):
    def __eq__(self, _other):
        raise AssertionError("untrusted provider identity equality was invoked")


@pytest.mark.parametrize("name", ["choose_adept_loan", "reconcile_adept_from_detail"])
def test_adept_gtk_scope_never_executes_hostile_identity_equality(name):
    host, _, requests = _host()
    book = _loan()
    _method(name)(host, "acquisition-a", book, object())
    (_label, _work, done, failed), _ = requests[-1]
    book.identity_id = HostileIdentity("alice")
    done({"status": "resolved"})
    failed(RuntimeError("PRIVATE"))
    _no_gtk(host)


def test_retired_send_mode_does_not_bind_or_call_acquisition():
    host, original, requests = _host()
    del original.reacquire_digital_loan
    _method("prepare_pnb_for_loan")(host, _loan(), send=True)
    work = requests[-1][0][1]
    with pytest.raises(RuntimeError, match="storage.target"):
        work()
