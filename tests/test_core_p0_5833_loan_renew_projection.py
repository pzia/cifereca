"""P0 #5833: renewal completion is delivered only to the original GTK view."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.models import Loan


def _renew_fn():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    method = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_renew"
    )
    unit = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), method], type_ignores=[],
    ))
    scope = {"TaskPriority": NS(USER_ACTION=7), "Loan": Loan}
    exec(compile(unit, str(source), "exec"), scope)
    return scope["_renew"]


def _host():
    jobs, receipt_views = [], []
    identity = NS(identity_id="alice")
    renew = Mock(return_value={
        "status": "submitted", "code": "renewal_provider_submitted",
        "provider_request_accepted": True, "provider_verified": False,
        "retry": "verification_only",
    })
    service = NS(
        current_identity=identity, _account_context_epoch=10,
        _source_context_epoch=20, renew=renew,
    )
    host = NS(
        service=service, _closed=False,
        _identity_context_generation=3, _account_context_generation=4,
        runner=NS(submit=lambda *args, **kwargs: jobs.append((args, kwargs))),
        _renew_result=lambda result: receipt_views.append(result),
    )
    loan = Loan(
        id="loan-a", title="Book", medium="physical",
        provider="branch", record_id="record", identity_id="alice",
        source_provider="paris", source_credential="card-a",
        source_account_id="account-a", state="active",
    )
    return host, service, renew, loan, jobs, receipt_views


def test_renewal_normal_delivery_preserves_one_loan_and_view():
    host, service, renew, loan, jobs, visible = _host()
    _renew_fn()(host, loan, None)
    assert len(jobs) == 1
    (label, work, done), options = jobs[0]
    assert options["kind"] == "loan-renew"
    assert options["priority"] == 7
    assert label == "Demande de prolongation…"
    result = work()
    renew.assert_called_once_with(loan, confirmed=True)
    done(result)
    assert visible == [result]


@pytest.mark.parametrize("mutation", [
    "selected_b", "aba", "account_epoch", "source_epoch",
    "gtk_identity_epoch", "gtk_account_epoch", "service_rebind", "closed",
])
def test_renewal_admitted_core_call_survives_navigation_but_no_stale_gtk_receipt(mutation):
    host, service, renew, loan, jobs, visible = _host()
    _renew_fn()(host, loan, None)
    assert len(jobs) == 1
    if mutation == "selected_b":
        service.current_identity = NS(identity_id="bob")
    elif mutation == "aba":
        service.current_identity = NS(identity_id="bob")
        service._account_context_epoch += 1
        service.current_identity = NS(identity_id="alice")
        service._account_context_epoch += 1
    elif mutation == "account_epoch":
        service._account_context_epoch += 1
    elif mutation == "source_epoch":
        service._source_context_epoch += 1
    elif mutation == "gtk_identity_epoch":
        host._identity_context_generation += 1
    elif mutation == "gtk_account_epoch":
        host._account_context_generation += 1
    elif mutation == "service_rebind":
        host.service = NS(
            current_identity=NS(identity_id="bob"),
            renew=Mock(side_effect=AssertionError("retargeted wrong service")),
        )
    else:
        host._closed = True
    (label, work, done), _options = jobs[0]
    result = work()
    renew.assert_called_once_with(loan, confirmed=True)
    done(result)
    assert visible == []


@pytest.mark.parametrize("mutation", ["wrong_identity", "rebound", "invalid_epoch", "closed"])
def test_unadmitted_renewal_never_schedules_any_worker(mutation):
    host, service, renew, loan, jobs, visible = _host()
    if mutation == "wrong_identity":
        service.current_identity = NS(identity_id="bob")
    elif mutation == "rebound":
        loan.identity_id = "bob"
    elif mutation == "invalid_epoch":
        service._source_context_epoch = "broken"
    else:
        host._closed = True
    _renew_fn()(host, loan, None)
    assert jobs == []
    renew.assert_not_called()
    assert visible == []


def test_renewal_captures_real_core_admission_before_queue():
    host, service, renew, loan, jobs, visible = _host()
    admission = object()
    service.capture_admitted_loan_renewal = Mock(return_value=admission)
    _renew_fn()(host, loan, None)
    service.capture_admitted_loan_renewal.assert_called_once_with(loan)
    assert len(jobs) == 1
    service.current_identity = NS(identity_id="bob")
    service._account_context_epoch += 1
    result = jobs[0][0][1]()
    renew.assert_called_once_with(loan, confirmed=True, admission=admission)
    jobs[0][0][2](result)
    assert visible == []


def test_renewal_core_admission_denied_never_enqueues():
    host, service, renew, loan, jobs, visible = _host()
    service.capture_admitted_loan_renewal = Mock(return_value=None)
    _renew_fn()(host, loan, None)
    assert not jobs
    renew.assert_not_called()
    assert visible == [{"status": "blocked", "code": "loan_scope_unverified"}]
