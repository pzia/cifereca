"""#4673: Paris catalogue availability must not claim account eligibility.

Headless: no GTK, credentials, provider network, or loan POST.
"""
import ast
from pathlib import Path

import pytest

from cifereca.gui.paris_borrow_status import (
    ACCOUNT_UNKNOWN, paris_borrow_status_message,
)

PANEL = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


@pytest.mark.parametrize(("reason", "fragment"), [
    ("identity_not_selected", "Sélectionnez le profil"),
    ("edition_or_source_unqualified", "édition ou source"),
    ("primary_paris_connection_mismatch_or_inactive", "connexion Paris principale"),
    ("local_edition_scope_invalid", "édition affichée"),
    ("local_provider_or_identity_unavailable", "fournisseur Paris"),
    ("local_session_unavailable", "session Paris"),
    ("local_primary_account_unverified", "compte Paris principal"),
    ("local_claim_scope_invalid", "réservation locale"),
    ("local_previous_attempt", "demande existe déjà"),
    ("local_scope_changed", "contexte du compte"),
    ("local_borrow_capability_unavailable", "capacité de prêt"),
    ("local_borrow_executor_unavailable", "service Paris connecté"),
    ("local_admission_unavailable", "admission locale"),
    ("local_action_available_provider_unknown", "demande explicite"),
])
def test_paris_known_local_reason_never_claims_provider_permission(reason, fragment):
    result = paris_borrow_status_message({
        "reason_code": reason, "provider_eligibility": "eligible",
        "signed_url": "https://private.invalid/secret",
    })
    assert fragment in result
    assert ACCOUNT_UNKNOWN in result
    assert "secret" not in result
    assert "private.invalid" not in result


@pytest.mark.parametrize("observation", [
    None, {}, [], "secret", {"reason_code": 123},
    {"reason_code": "secret-provider-refused-account-XYZ"},
])
def test_unknown_and_malformed_diagnostics_fail_closed(observation):
    assert paris_borrow_status_message(observation) == ACCOUNT_UNKNOWN


def test_search_detail_keeps_raw_availability_for_action_gate():
    source = PANEL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    panel = next(n for n in tree.body
                 if isinstance(n, ast.ClassDef) and n.name == "DetailPanel")
    method = next(n for n in panel.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "show_item_detail")
    # Product wording cannot silently suppress the same Core action just
    # because the text now mentions the catalogue rather than the account.
    candidate_calls = [n for n in ast.walk(method)
                       if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name)
                       and n.func.id == "search_detail_borrow_action_candidate"]
    assert len(candidate_calls) == 1
    assert len(candidate_calls[0].args) == 2
    assert isinstance(candidate_calls[0].args[1], ast.Name)
    assert candidate_calls[0].args[1].id == "availability"
    assert "Empruntable selon le catalogue · compte non vérifié" in source
    assert '("Compte Paris", "Éligibilité du prêt non vérifiée par le fournisseur")' in source
    assert "paris_borrow_status_message(diagnostic)" in source
