"""#7244 historical ledger diagnostic labels are never metadata authority."""
import pytest

from cifereca.gui.adept_historical_maintenance_line import _display


@pytest.mark.parametrize("field", [
    "acquisition_id", "provider_id", "credential_id", "account_id",
    "edition_id", "provider_loan_id", "recorded_adept_profile_id",
    "recorded_adept_loan_id", "canonical_branch",
])
@pytest.mark.parametrize("unsafe", [
    "loan\nFORGED", "loan\x85FORGED", "loan\u202eFORGED",
    "loan\u2028FORGED", "loan\u200dFORGED", "x" * 1025,
])
def test_ledger_ui_redacts_unsafe_historical_coordinate_without_mutating_row(field, unsafe):
    row = {
        "acquisition_id": "acquisition",
        "provider_id": "provider",
        "credential_id": "credential",
        "account_id": "",
        "edition_id": "edition",
        "provider_loan_id": "loan",
        "recorded_adept_profile_id": "profile",
        "recorded_adept_loan_id": "adept-loan",
        "canonical_branch": "branch",
        "ledger_status": "complete",
        "canonical_status": "unique_source_record",
        "historical_license_state": "unknown",
        "publication": None, "local_bytes": None,
        "inventory_checked_at": 12345,
        "may_reacquire": False, "may_rebind_or_return": False,
    }
    row[field] = unsafe
    prior = dict(row)
    rendered = _display({"rows": (row,)})
    assert "valeur non affichable" in rendered
    assert "FORGED" not in rendered
    assert unsafe not in rendered
    assert "Horodatage historique (Unix) : 12345" in rendered
    assert "Aucun retour, rattachement, acquisition ou suppression autorisé" in rendered
    assert row == prior
    assert row["may_reacquire"] is False and row["may_rebind_or_return"] is False


def test_historical_ledger_diagnostic_preserves_legitimate_empty_account_and_exact_text():
    report = {"rows": ({
        "acquisition_id": "acquisition",
        "provider_id": "provider",
        "credential_id": "credential",
        "account_id": "",
        "edition_id": "edition",
        "provider_loan_id": "loan",
        "recorded_adept_profile_id": None,
        "recorded_adept_loan_id": None,
        "canonical_branch": "branch",
        "ledger_status": "complete",
        "canonical_status": "unique_source_record",
        "historical_license_state": "unknown",
        "publication": {}, "local_bytes": {},
        "inventory_checked_at": None,
    },)}
    shown = _display(report)
    assert "provider / credential / (vide enregistré)" in shown
    assert "Édition : edition · Prêt : loan" in shown
    assert "Branche canonique candidate : branch" in shown
    assert "Aucun retour, rattachement, acquisition ou suppression autorisé" in shown
