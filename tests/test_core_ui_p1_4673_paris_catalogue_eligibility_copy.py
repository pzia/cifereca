"""#4673: a catalogue availability label must not attest loan eligibility.

Headless static regression: detail rendering must not issue a loan,
reconstruct provider permissions or disclose diagnostic payloads.
"""
import ast
from pathlib import Path

PANEL = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


def test_search_detail_remains_non_authorizing_and_keeps_action_gate():
    source = PANEL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    panel = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in panel.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "show_item_detail")
    candidate_calls = [node for node in ast.walk(method)
                       if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name)
                       and node.func.id == "search_detail_borrow_action_candidate"]
    assert len(candidate_calls) == 1
    assert len(candidate_calls[0].args) == 2
    assert isinstance(candidate_calls[0].args[1], ast.Name)
    assert candidate_calls[0].args[1].id == "availability"
    assert "Empruntable selon le catalogue · compte non vérifié" in source
    assert '("Compte Paris", "Éligibilité du prêt non vérifiée par le fournisseur")' in source
    assert "Éligibilité du compte non vérifiée auprès du fournisseur." in source
    assert "paris_borrow_status_message" not in source
    assert "paris_borrow_diagnostic" not in source


def test_uncertain_existing_loan_exposes_read_only_followup_without_new_post():
    source = PANEL.read_text(encoding="utf-8")
    assert "if pending_followup:" in source
    assert "Demande de prêt Paris déjà envoyée" in source
    assert "Ne réempruntez pas." in source
    assert "Vérifier le prêt sans réemprunter" in source
    assert "cifereca-borrow-followup" in source
