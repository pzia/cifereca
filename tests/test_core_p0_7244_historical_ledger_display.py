"""#7244 historical ADEPT ledger review never renders untrusted control labels."""

from copy import deepcopy

import pytest

from cifereca.gui.adept_historical_maintenance_line import _display


def _page():
    return {"rows": ({
        "acquisition_id": "acquisition",
        "ledger_status": "complete",
        "provider_id": "provider",
        "credential_id": "credential",
        "account_id": "",
        "edition_id": "edition",
        "provider_loan_id": "loan",
        "recorded_adept_profile_id": "historic-profile",
        "recorded_adept_loan_id": "historic-loan",
        "historical_license_state": "active_recorded",
        "canonical_branch": "loans",
        "canonical_status": "unique_source_record",
        "inventory_checked_at": 1234567890,
        "publication": {
            "canonical_origin_records": 1,
            "canonical_placement_state": "observed",
        },
        "local_bytes": {"evidence": {"placement_evidence": "verified_artifact_bytes"}},
    },)}


@pytest.mark.parametrize("field", [
    "acquisition_id", "provider_id", "credential_id", "edition_id",
    "provider_loan_id", "recorded_adept_profile_id",
    "recorded_adept_loan_id", "canonical_branch",
    "publication.canonical_placement_state", "local_bytes.evidence.placement_evidence",
])
@pytest.mark.parametrize("hidden", ["\x85", "\u202e", "\u2028", "\u200d"])
def test_historical_ledger_untrusted_labels_cannot_spoof_report_lines(field, hidden):
    page = deepcopy(_page())
    row = page["rows"][0]
    value = "private" + hidden + "FORGED"
    target = row
    parts = field.split(".")
    for key in parts[:-1]:
        target = target[key]
    target[parts[-1]] = value
    output = _display(page)
    assert "FORGED" not in output
    assert "valeur non affichable" in output
    assert "aucun état fournisseur actuel vérifié" in output
    assert "Aucun retour, rattachement, acquisition ou suppression autorisé" in output
    assert "Horodatage historique (Unix) : 1234567890" in output
    assert "(vide enregistré)" in output


def test_historical_ledger_keeps_readable_labels_and_rejects_malformed_rows():
    page = _page()
    rendered = _display(page)
    assert "Édition : edition · Prêt : loan" in rendered
    assert "Origines : 1 · Placement : observed" in rendered
    assert "Horodatage historique (Unix) : 1234567890" in rendered
    assert "valeur non affichable" not in rendered
    assert "indisponible" in _display({"rows": ("not a row",)})
    page["rows"][0]["publication"] = "malformed"
    page["rows"][0]["local_bytes"] = "malformed"
    rendered = _display(page)
    assert "not_checked" in rendered
    assert "malformed" not in rendered
