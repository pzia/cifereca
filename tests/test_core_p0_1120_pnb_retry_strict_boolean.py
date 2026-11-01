"""P1 #4048: retired PNB returns cannot be reopened by any retry input.

Synthetic loan/provider fixtures only; no HTTP, ADEPT or remote mutation.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.core import provider_loan_return
from tests.test_core_0982_loan_return_reconciliation import (
    Session, _library, _loan,
)
from tests.test_core_0984_adept_first_paris_reconcile import (
    setup as adobe_setup, stub_adept,
)


class _HostileOverride:
    def __bool__(self):
        raise AssertionError("foreign explicit_retry must not be truth-tested")

    def __str__(self):
        raise AssertionError("foreign explicit_retry must not be serialized")


def service_logger():
    import logging
    return logging.getLogger("cifereca.tests.p1-return-retired")


DENIED = {"status": "blocked", "code": "loan_return_removed", "retry": "none"}


@pytest.mark.parametrize("untrusted", [
    "false", "true", "1", 1, [True], {"retry": True}, _HostileOverride(),
])
def test_legacy_retry_never_posts_or_claims_even_without_prior_checkpoint(tmp_path, untrusted):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        before = service.loan_return_state(loan)
        assert service.return_digital(loan) == DENIED
        assert service.return_digital(loan, explicit_retry=untrusted) == DENIED
        assert session.posts == [] and session.checks == []
        assert service.loan_return_state(loan) == before
    finally:
        service.close()


@pytest.mark.parametrize("untrusted", ["false", 1, _HostileOverride()])
def test_direct_provider_helper_cannot_reopen_a_retired_return(tmp_path, untrusted):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        before = service.loan_return_state(loan)
        assert provider_loan_return.return_digital(
            service, loan, explicit_retry=untrusted, _TRACE_LOG=service_logger(),
        ) == DENIED
        assert session.posts == [] and session.checks == []
        assert service.loan_return_state(loan) == before
    finally:
        service.close()


def test_adobe_first_path_has_no_fallback_to_provider_return(tmp_path):
    service, loan, paris = adobe_setup(tmp_path, paris_active=True)
    try:
        adobe = stub_adept(service)
        assert service.return_loan_prefer_adept(loan) == DENIED
        for value in ("false", _HostileOverride(), True):
            assert service.return_digital(loan, explicit_retry=value) == DENIED
        assert adobe["mutations"] == 0
        assert paris.posts == 0
    finally:
        service.close()


def test_native_true_never_reauthorizes_return_after_removal(tmp_path):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        before = service.loan_return_state(loan)
        assert service.return_digital(loan, explicit_retry=True) == DENIED
        assert session.posts == [] and session.checks == []
        assert service.loan_return_state(loan) == before
    finally:
        service.close()


def test_provider_helper_has_no_retry_or_checkpoint_mutation_code():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/core/provider_loan_return.py"
    method = next(
        n for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, ast.FunctionDef) and n.name == "return_digital"
    )
    body = ast.unparse(method)
    assert "loan_return_removed" in body
    for forbidden in (
        "loan_return_claim", "verify_return_digital", "session.return_digital",
        "self.storage", "explicit_retry is True", "ReturnDigitalLoan",
    ):
        assert forbidden not in body
