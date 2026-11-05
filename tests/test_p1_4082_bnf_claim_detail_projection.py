"""P1 #4082: exact read-only BnF checkpoint → human-readable book detail.

Real Core SQLite plus synthetic session; no BnF HTTP, borrow POST, retries
or mutation from the UI projection. The old one-shot ledger stays intact.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.bnf_borrow_claim_observation import BnfBorrowClaimObservation
from cifereca.gui.bnf_borrow_claim_status import bnf_borrow_claim_message
from tests.test_core_p0_2055_manual_dispatch import setup


@pytest.mark.parametrize("status,accepted,loan,expected", [
    ("request_sent", False, None, "Une tentative antérieure"),
    ("submitted", True, None, "Une demande antérieure"),
    ("verification_unavailable", False, None, "n’a pas pu être vérifiée"),
    ("blocked", False, None, "Une tentative précédente"),
    ("confirmed", True, "loan-synthetic", "avait été confirmé"),
])
def test_exact_scoped_checkpoint_reaches_ui_without_provider_calls_or_rearm(
    tmp_path, status, accepted, loan, expected,
):
    service, session, account, item = setup(tmp_path)
    try:
        key = borrow_claim_key(account, item)
        assert service.storage.loan_borrow_claim_once(key, "attempt-once")[0]
        if status != "request_sent":
            service.storage.loan_borrow_finish(
                key, "attempt-once", status=status, accepted=accepted,
                verified_loan_id=loan,
            )
        prior = service.storage.metadata_get(key)
        state = service.bnf_borrow_claim_observation(
            item, expected_identity_id="default",
        )
        assert state.state == status
        assert state.claim_consumed is True
        assert state.retry_authorized is False
        message = bnf_borrow_claim_message(state)
        assert expected in message
        assert "attempt-once" not in message
        assert "loan-synthetic" not in message
        assert "credential" not in message
        assert session.reads == session.dispatches == []
        assert service.storage.metadata_get(key) == prior
        assert service.storage.loan_borrow_claim_once(key, "replay")[0] is False
    finally:
        service.close()


def test_without_checkpoint_does_not_claim_borrowability_or_write_sqlite(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        key = borrow_claim_key(account, item)
        state = service.bnf_borrow_claim_observation(
            item, expected_identity_id="default",
        )
        assert state.state == "no_checkpoint"
        assert state.retry_authorized is False
        assert bnf_borrow_claim_message(state) is None
        assert service.storage.metadata_get(key) is None
        assert session.reads == session.dispatches == []
    finally:
        service.close()


def test_identity_account_edition_and_primary_binding_fail_closed(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        service.storage.loan_borrow_claim_once(borrow_claim_key(account, item), "once")
        wrong = [
            (item, "other"),
            (replace(item, source_provider="paris"), "default"),
            (replace(item, medium="physical"), "default"),
            (replace(item, docbase="OTHER"), "default"),
            (replace(item, source_credential="other"), "default"),
        ]
        for notice, identity in wrong:
            state = service.bnf_borrow_claim_observation(
                notice, expected_identity_id=identity,
            )
            assert state.state == "scope_unverified"
            assert bnf_borrow_claim_message(state) is None
        other_edition = service.bnf_borrow_claim_observation(
            replace(item, record_id="different"), expected_identity_id="default",
        )
        assert other_edition.state == "no_checkpoint"
        service.accounts.append(replace(account))
        assert service.bnf_borrow_claim_observation(
            item, expected_identity_id="default",
        ).state == "scope_unverified"
        service.accounts.pop()
        account.is_primary = False
        assert service.bnf_borrow_claim_observation(
            item, expected_identity_id="default",
        ).state == "scope_unverified"
        assert session.reads == session.dispatches == []
    finally:
        service.close()


def test_ui_messages_require_a_closed_consistent_claim():
    assert bnf_borrow_claim_message(None) is None
    for state in ("secret", "no_checkpoint", "read_unavailable", "scope_unverified"):
        assert bnf_borrow_claim_message(
            BnfBorrowClaimObservation(state, None, False),
        ) is None
    assert bnf_borrow_claim_message(
        BnfBorrowClaimObservation("confirmed", True, False),
    ) is None
    assert bnf_borrow_claim_message(
        BnfBorrowClaimObservation("confirmed", True, True, retry_authorized=True),
    ) is None
    assert bnf_borrow_claim_message(
        BnfBorrowClaimObservation("request_sent", False, False),
    ) is None
    assert bnf_borrow_claim_message(
        BnfBorrowClaimObservation("opaque_consumed", True, False),
    ).startswith("Une tentative antérieure")


def test_actual_detail_panel_uses_core_readonly_observer_not_provider_or_claim_reset():
    panel_source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(panel_source)
    detail = next(
        node for owner in tree.body if isinstance(owner, ast.ClassDef)
        and owner.name == "DetailPanel"
        for node in owner.body if isinstance(node, ast.FunctionDef)
        and node.name == "show_item_detail"
    )
    source = ast.unparse(detail)
    assert "bnf_borrow_claim_observation" in source
    assert "bnf_borrow_claim_message" in source
    assert "item.source_provider == 'bnf-pret'" in source
    assert "row.set_subtitle(explanation)" in source
    assert "loan_borrow_finish" not in source
    assert "loan_borrow_claim_once" not in source
    assert "session.borrow" not in source
