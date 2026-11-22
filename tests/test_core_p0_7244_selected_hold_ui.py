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
