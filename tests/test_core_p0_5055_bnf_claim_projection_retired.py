"""#5055: obsolete BnF detail projection stays deleted; Core owns loan guards."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bnf_claim_detail_projection_is_absent_without_changing_loan_guards():
    gui = ROOT / "src/cifereca/gui"
    assert not (gui / "bnf_borrow_claim_status.py").exists()
    detail = (gui / "detail_panel.py").read_text(encoding="utf-8")
    assert "bnf_borrow_claim_status" not in detail
    assert "bnf_borrow_claim_message" not in detail
    # Unverified availability and a consumed claim must never invite a retry.
    assert "Une demande antérieure peut exister : consultez Mes emprunts." in detail
    assert "Cifereca ne répétera pas automatiquement la demande." in detail
    assert "Une demande BnF existe peut-être déjà" not in detail
    # #5387: Paris eligibility diagnostics must not be a CoreUI fallback.
    # This is independent from the BnF anti-replay guarantees above.
    assert "paris_borrow_diagnostic" not in detail
    assert "Éligibilité du compte non vérifiée auprès du fournisseur." in detail
    assert "Vérifier le prêt sans réemprunter" in detail
    # Core's checkpoint and anti-replay are retained independently of UI text.
    core = ROOT / "src/cifereca/core"
    assert (core / "borrow_claim_observation.py").is_file()
    assert (core / "borrow_action_scope.py").is_file()
