"""Selected hold report remains negative-only, even with exact evidence."""
import pytest
from cifereca.core import adept_historical_maintenance as review
from cifereca.core import canonical_adept_attempt as marker
from cifereca.gui.adept_historical_maintenance_line import _selected_hold_summary
from tests.test_core_p0_976_historical_maintenance import _setup, store, BYTES


@pytest.mark.parametrize("proven", [True, False])
def test_selected_hold_report_explains_reason_without_marker_or_unlock(store, tmp_path, proven):
    service, source, path, _ = _setup(store, tmp_path)
    raw = marker._value("private-offer", "a" * 32)
    store.metadata_set(marker._key(source), raw)
    if proven:
        store.metadata_set(marker._unsubmitted_key(source),
            marker._unsubmitted_value(source, raw, "core_pre_dispatch_refusal"))
    request = review.admit_historical_review(service)
    page = review.run_historical_hold_review(service, request)
    ordinal = next(i for i, item in enumerate(page["rows"])
                   if item["provider_loan_id"] == source.right_id)
    selected = review.select_historical_hold(service, request, page, ordinal=ordinal, confirmed=True)
    assert selected is not None
    text = _selected_hold_summary(selected)
    assert source.right_id in text and source.scope.provider_id in text
    assert "Retéléchargement non autorisé" in text
    assert ("non-soumission enregistré" in text) == proven
    assert raw not in text and "private-offer" not in text and "périmée" in _selected_hold_summary(None)
    assert store.metadata_get(marker._key(source)) == raw and path.read_bytes() == BYTES


@pytest.mark.parametrize("unsafe_text", [
    "prefix\tFORGED", "prefix\x85FORGED", "prefix\u202eFORGED",
    "prefix\u2028FORGED", "prefix\u200dFORGED",
])
@pytest.mark.parametrize("field", [
    "provider_id", "credential_id", "account_id", "edition_id",
    "right_id", "branch",
])
def test_historical_hold_display_sanitizes_exact_control_character_coordinates(field, unsafe_text):
    from types import SimpleNamespace
    from cifereca.core.source_convergence import SourceKey
    from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
    from cifereca.gui.adept_historical_maintenance_line import _display_holds

    scope = dict(identity_id="identity", provider_id="provider",
                 credential_id="credential", account_id="")
    coordinates = dict(edition_id="edition", right_id="loan",
                       kind="loan", branch="branch")
    invalid = unsafe_text
    if field in scope:
        scope[field] = invalid
    else:
        coordinates[field] = invalid
    source = SourceKey(ProviderRightsScope(**scope), **coordinates)
    report = {"known_holds": 1, "mutation_authorized": False, "rows": ({
        "title": "Référence de prêt historique",
        "provider_id": source.scope.provider_id,
        "credential_id": source.scope.credential_id,
        "account_id": source.scope.account_id,
        "edition_id": source.edition_id,
        "provider_loan_id": source.right_id,
        "canonical_branch": source.branch,
        "hold_origin": "marker_recorded",
        "marker_state": "held_phase_unknown",
        "non_submission_evidence": "not_recorded",
        "local_copy_evidence": "not_requested",
        "recorded_inactive": False,
        "unlock_authorized": False,
    },)}
    page_text = _display_holds(report)
    selected = review.HistoricalHoldSelection(
        request=None, source=source,
        marker=SimpleNamespace(state="held_phase_unknown"),
        witness=SimpleNamespace(state="not_recorded"),
        stamp=(), copy_evidence="not_requested",
    )
    summary_text = _selected_hold_summary(selected)
    for shown in (page_text, summary_text):
        assert "identifiant non affichable" in shown
        assert "FORGED" not in shown and "\t" not in shown
        assert "Retéléchargement non autorisé" in shown
    assert not selected.unlock_authorized and not report["mutation_authorized"]


def test_empty_primary_account_remains_visible_without_new_binding():
    from cifereca.gui.adept_historical_maintenance_line import _hold_coordinate
    assert _hold_coordinate("", account=True) == "(compte principal)"
    assert _hold_coordinate("") == "(identifiant non affichable)"
    assert _hold_coordinate("edition") == "edition"
