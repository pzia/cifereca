"""P1 #4048: former PNB retry flags cannot revive removed voluntary returns.

The original P0 #1120 at-most-once guarantee remains stronger: zero new
returns, even for native True, hostile truthy values or a direct helper.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.core import provider_loan_return
from cifereca.plugin_api import AttentionRequired
from tests.test_core_0982_loan_return_reconciliation import Session, _library, _loan
from tests.test_core_0984_adept_first_paris_reconcile import (
    setup as adobe_setup, stub_adept,
)


class _HostileOverride:
    def __bool__(self):
        raise AssertionError("retired explicit_retry must not be truth-tested")

    def __str__(self):
        raise AssertionError("retired explicit_retry must not be serialized")


@pytest.mark.parametrize("retry", [
    False, True, "false", "true", "1", 1, [True],
    {"retry": True}, _HostileOverride(),
])
def test_retired_pnb_return_refuses_every_retry_flag_without_io(tmp_path, retry):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired) as exc:
            service.return_digital(loan, explicit_retry=retry)
        assert exc.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        assert service.loan_return_state(loan) is None
    finally:
        service.close()


@pytest.mark.parametrize("retry", [False, True, "false", 1, _HostileOverride()])
def test_direct_provider_helper_does_not_bypass_retirement(tmp_path, retry):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired) as exc:
            provider_loan_return.return_digital(
                service, loan, explicit_retry=retry,
                _TRACE_LOG=_HostileOverride(),
            )
        assert exc.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        assert service.loan_return_state(loan) is None
    finally:
        service.close()


@pytest.mark.parametrize("retry", ["false", True, _HostileOverride()])
def test_retired_adept_first_and_pnb_routes_cannot_be_reopened(tmp_path, retry):
    service, loan, paris = adobe_setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service)
        with pytest.raises(AttentionRequired) as adobe:
            service.return_loan_prefer_adept(loan)
        with pytest.raises(AttentionRequired) as pnb:
            service.return_digital(loan, explicit_retry=retry)
        assert adobe.value.code == pnb.value.code == "loan_return_removed"
        assert calls["mutations"] == 0
        assert paris.posts == 0
        assert service.loan_return_state(loan) is None
    finally:
        service.close()


def test_return_helper_has_no_reachable_retry_claim_or_mutation():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/core/provider_loan_return.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    method = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "return_digital"
    )
    assert isinstance(method.body[1], ast.Raise)
    source = ast.unparse(method)
    assert "loan_return_removed" in source
    assert "loan_return_claim" not in source
    assert "verify_return_digital" not in source
    assert "session.return_digital" not in source
