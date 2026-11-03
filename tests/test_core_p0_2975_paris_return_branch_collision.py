"""P0 #2975: same-ID Paris branch collision is not evidence of return.

PNB and ADEPT-first verify only already-requested transactions; no provider
traffic, duplicate PNB POST, Adobe mutation, EPUB deletion or log secrets.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from dataclasses import replace
import json

import pytest
from cifereca.plugin_api import AttentionRequired

from cifereca.core.loan_return_verification import _paris_loan_presence
from tests.test_core_0982_loan_return_reconciliation import (
    _library, _loan, Session,
)
from tests.test_core_0984_adept_first_paris_reconcile import setup as adept_setup


def test_retired_pnb_does_not_mutate_on_cross_branch_collision(tmp_path):
    loan = _loan()
    other_branch = replace(
        loan, provider="SYRACUSE", medium="physical",
        title="PRIVATE_SYRACUSE_TITLE",
    )
    session = Session([other_branch])
    service = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired) as retired:
            service.return_digital(loan)
        assert retired.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        assert service.loan_return_state(loan) is None
    finally:
        service.close()


def test_adept_first_same_id_in_other_paris_branch_is_unverified(tmp_path):
    service, loan, paris = adept_setup(tmp_path)
    paris.inventory = [replace(
        loan, provider="SYRACUSE", medium="physical",
        title="PRIVATE_CROSS_BRANCH",
    )]
    try:
        result = service.verify_paris_return_inventory(loan)
        assert result == {
            "status": "verification_unavailable",
            "verification": "unavailable",
        }
        assert paris.posts == 0
        assert paris.accounts_read == ["linked-123"]
        assert "PRIVATE_CROSS_BRANCH" not in json.dumps(result)
        paris.inventory = [loan, *paris.inventory]
        present = service.verify_paris_return_inventory(loan)
        assert present["status"] == "still_active"
        assert paris.posts == 0
    finally:
        service.close()


def test_presence_rule_is_exact_branch_and_does_not_infer_absence():
    loan = _loan()
    unrelated = replace(loan, id="other-id")
    other_branch = replace(loan, provider="SYRACUSE")
    assert _paris_loan_presence([unrelated], loan) is False
    assert _paris_loan_presence([other_branch], loan) is None
    assert _paris_loan_presence([loan, other_branch], loan) is True
    assert _paris_loan_presence([], loan) is False
    # Same supplier label does not override a conflicting medium.
    assert _paris_loan_presence([
        replace(loan, medium="physical"),
    ], loan) is None
    assert _paris_loan_presence([
        replace(loan, medium="physical"), loan,
    ], loan) is True



def test_retired_pnb_cannot_mutate_when_same_id_has_other_medium(tmp_path):
    loan = _loan()
    other_medium = replace(
        loan, medium="physical", title="SYNTHETIC_PHYSICAL_ONLY",
    )
    session = Session([other_medium])
    service = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired) as retired:
            service.return_digital(loan)
        assert retired.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        assert service.loan_return_state(loan) is None
    finally:
        service.close()


def test_adept_first_same_provider_id_different_medium_is_unverified(tmp_path):
    service, loan, paris = adept_setup(tmp_path)
    paris.inventory = [replace(
        loan, medium="physical", title="SYNTHETIC_DIFFERENT_MEDIUM",
    )]
    try:
        result = service.verify_paris_return_inventory(loan)
        assert result == {
            "status": "verification_unavailable",
            "verification": "unavailable",
        }
        assert paris.posts == 0
        assert "SYNTHETIC_DIFFERENT_MEDIUM" not in json.dumps(result)
        paris.inventory = [loan, *paris.inventory]
        assert service.verify_paris_return_inventory(loan)["status"] == "still_active"
        assert paris.posts == 0
    finally:
        service.close()
