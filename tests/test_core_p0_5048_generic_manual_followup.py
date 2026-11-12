"""P0 #5048: spent borrow claims use only the typed, source-neutral ABI.

Offline Core/SQLite with synthetic BnF session, zero supplier transport.
"""
from dataclasses import replace
from pathlib import Path
import ast

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p0_2055_manual_dispatch import setup


def test_fictional_bnf_claim_followup_requires_exact_spent_checkpoint(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        before = service.manual_borrow_diagnostic(item)
        assert before["local_action_possible"] is True
        assert before["provider_eligibility"] == "unknown"
        assert not service.manual_borrow_followup_available(item)

        key = borrow_claim_key(account, item)
        assert key
        claimed, _receipt = service.storage.loan_borrow_claim_once(
            key, "offline-spent-bnf-claim",
        )
        assert claimed is True
        assert service.storage.loan_borrow_finish(
            key, "offline-spent-bnf-claim",
            status="verification_unavailable",
        ) is not None
        assert service.manual_borrow_diagnostic(item)["reason_code"] == (
            "local_previous_attempt"
        )
        assert service.manual_borrow_followup_available(item) is True
        assert service.can_borrow_manual(item) is False

        # Unrelated edition or credential never reuses the claim.
        assert not service.manual_borrow_followup_available(
            replace(item, record_id="other-edition"),
        )
        assert not service.manual_borrow_followup_available(
            replace(item, source_credential="other-credential"),
        )
        assert not service.manual_borrow_followup_available(
            replace(item, docbase="UNKNOWN"),
        )
        # A separate observation can never replay a mutating executor.
        session.borrow_explicit_with_claim = lambda *_a, **_k: (
            1 / 0
        )
        result = service.verify_manual_borrow_followup(item)
        assert result["status"] in {"unavailable", "blocked"}
        assert result["mutated"] is False
        assert result["retry_borrow"] is False
        assert service.storage.loan_borrow_state(key)["status"] == (
            "verification_unavailable"
        )
        assert session.dispatches == []
        assert service.storage.entitlements(provider_id="bnf-pret") == []
    finally:
        service.close()


def test_service_has_no_paris_only_followup_facades():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "src/cifereca/services.py").read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "LibraryService")
    methods = {n.name for n in owner.body if isinstance(n, ast.FunctionDef)}
    assert "paris_borrow_followup_available" not in methods
    assert "verify_paris_borrow_followup" not in methods
    assert {
        "manual_borrow_diagnostic", "manual_borrow_followup_available",
        "verify_manual_borrow_followup",
    } <= methods
    gui = (root / "src/cifereca/gui/manual_borrow_controller.py").read_text()
    assert "def present_paris_borrow_verification(" not in gui
    assert "def present_manual_borrow_verification(" in gui
