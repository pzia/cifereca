"""#1120: a non-attested Adobe checkpoint cannot license a PNB return.

No Paris/Adobe network, ACSM or real loan: all activity uses synthetic
SQLite receipts and fake sessions. Existing fresh verification and explicit
native True semantics remain covered.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from cifereca.plugin_api import AttentionRequired

from cifereca.core.loan_return_guard import return_key
from cifereca.core.return_adept_orchestrator import (
    adept_return_key, adobe_return_attested, public_state,
)
from tests.test_core_0982_loan_return_reconciliation import (
    _library, _loan, Session,
)


def _adobe_checkpoint(service, loan, *, status, adobe_verified=True):
    key = adept_return_key(
        loan.source_provider, loan.source_credential,
        loan.source_account_id, loan.id,
    )
    claimed, _ = service.storage.loan_return_claim(key, "synthetic-adobe")
    assert claimed
    row = service.storage.loan_return_transition(
        key, "synthetic-adobe", status=status,
        verification="adobe_confirmed", adobe_verified=adobe_verified,
    )
    assert row is not None
    return row


@pytest.mark.parametrize("unattested_phase", [
    "request_sent", "adobe_return_requested", "failed",
])
def test_explicit_pnb_action_does_not_trust_bare_adobe_boolean(
    tmp_path, unattested_phase,
):
    loan = _loan()
    paris = Session([loan])
    svc = _library(tmp_path, session=paris)
    try:
        adobe = _adobe_checkpoint(
            svc, loan, status=unattested_phase, adobe_verified=True,
        )
        assert adobe["adobe_verified"] is True
        assert not adobe_return_attested(adobe)
        assert public_state(adobe)["adobe_return_confirmed"] is False
        with pytest.raises(AttentionRequired) as failure:
            svc.return_digital(loan, explicit_retry=True)
        assert failure.value.code == "loan_return_removed"
        assert paris.posts == paris.checks == []
        assert svc.storage.loan_return_state(
            return_key("paris", "paris-a", "linked-b", loan.id),
        ) is None
        assert svc.storage.loan_return_state(
            adept_return_key("paris", "paris-a", "linked-b", loan.id),
        ) == adobe
    finally:
        svc.close()


def test_verified_adobe_phase_keeps_separate_explicit_pnb_action(tmp_path):
    loan = _loan()
    paris = Session([loan])
    svc = _library(tmp_path, session=paris)
    try:
        adobe = _adobe_checkpoint(
            svc, loan, status="adobe_return_confirmed", adobe_verified=True,
        )
        assert adobe_return_attested(adobe)
        for retry in (False, True):
            with pytest.raises(AttentionRequired) as failure:
                svc.return_digital(loan, explicit_retry=retry)
            assert failure.value.code == "loan_return_removed"
        assert paris.posts == paris.checks == []
        assert svc.storage.loan_return_state(
            adept_return_key("paris", "paris-a", "linked-b", loan.id),
        ) == adobe
        assert svc.storage.loan_return_state(
            return_key("paris", "paris-a", "linked-b", loan.id),
        ) is None
    finally:
        svc.close()


def test_provider_return_entry_is_inert_before_any_adobe_or_pnb_check():
    path = (Path(__file__).resolve().parents[1] /
            "src/cifereca/core/provider_loan_return.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    operation = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "return_digital")
    assert isinstance(operation.body[1], ast.Raise)
    source = ast.unparse(operation)
    assert "loan_return_removed" in source
    for forbidden in (
        "adobe_return_attested", "verify_paris_return_inventory",
        "loan_return_claim", "session.return_digital",
    ):
        assert forbidden not in source
