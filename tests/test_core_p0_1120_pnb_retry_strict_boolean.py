"""P0 #1120: only an explicit native True can reopen a PNB return.

Synthetic Paris and Adobe fixtures only: no external connection, ACSM,
license, real return, download or persistent user account is involved.
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


@pytest.mark.parametrize("untrusted", [
    "false", "true", "1", 1, [True], {"retry": True}, _HostileOverride(),
])
def test_untrusted_retry_cannot_repost_after_existing_pnb_claim(
    tmp_path, untrusted,
):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        assert service.return_digital(loan)["status"] == "still_active"
        prior = service.loan_return_state(loan)
        assert len(session.posts) == len(session.checks) == 1

        outcome = service.return_digital(loan, explicit_retry=untrusted)
        assert outcome == prior
        assert len(session.posts) == len(session.checks) == 1
        assert service.loan_return_state(loan) == prior
    finally:
        service.close()


@pytest.mark.parametrize("untrusted", ["false", 1, _HostileOverride()])
def test_direct_guarded_helper_cannot_reopen_prior_claim(tmp_path, untrusted):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        service.return_digital(loan)
        before = service.loan_return_state(loan)
        assert provider_loan_return.return_digital(
            service, loan, explicit_retry=untrusted, _TRACE_LOG=service_logger(),
        ) == before
        assert len(session.posts) == len(session.checks) == 1
    finally:
        service.close()


def service_logger():
    import logging
    return logging.getLogger("cifereca.tests.p0-return-retry")


def test_untyped_retry_never_falls_through_adept_first_provider_precedence(
    tmp_path,
):
    service, loan, paris = adobe_setup(tmp_path, paris_active=True)
    try:
        adept_calls = stub_adept(service)
        first = service.return_loan_prefer_adept(loan)
        assert first["status"] == "partial"
        assert first["adobe_return_confirmed"] is True
        assert first["paris_verification"] == "still_present"
        assert adept_calls["mutations"] == 1
        assert paris.posts == 0
        assert service.return_digital(loan, explicit_retry="false")["reason"] == (
            "adobe_return_takes_precedence"
        )
        assert service.return_digital(loan, explicit_retry=_HostileOverride())[
            "reason"
        ] == "adobe_return_takes_precedence"
        assert paris.posts == 0
        assert adept_calls["mutations"] == 1
        # Native True continues to require a fresh read and still permits the
        # separate user-authorized PNB action after Adobe confirmation.
        assert service.return_digital(loan, explicit_retry=True)["status"] == (
            "still_active"
        )
        assert paris.posts == 1
        assert adept_calls["mutations"] == 1
    finally:
        service.close()


def test_native_true_keeps_existing_fresh_verify_and_repost_behavior(tmp_path):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        assert service.return_digital(loan)["status"] == "still_active"
        assert service.return_digital(loan, explicit_retry=True)["status"] == (
            "still_active"
        )
        assert len(session.posts) == 2
        assert len(session.checks) == 3
    finally:
        service.close()


def test_boolean_gate_precedes_any_return_preflight_or_sqlite_claim():
    path = (Path(__file__).resolve().parents[1] /
            "src/cifereca/core/provider_loan_return.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    method = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "return_digital")
    normalization = next(node for node in method.body
                         if isinstance(node, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "explicit_retry"
                                 for t in node.targets))
    assert ast.unparse(normalization.value) == "explicit_retry is True"
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "self.storage.loan_return_claim"]
    assert len(calls) == 1 and normalization.lineno < calls[0].lineno
    assert "loan_return_state" in ast.unparse(method)
    assert "verify_return_digital" in ast.unparse(method)
