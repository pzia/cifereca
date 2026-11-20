"""Public Numilog refinement and detail share the same anonymous exact row."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public_detail import validate_public_detail_item
from cifereca_numilog_public_local_filter import refine_loaded_public_page
from test_numilog_public_local_refinement_integrity_p0 import page


@pytest.mark.parametrize("changes", [
    {"source_account_id": "other-account"},
    {"source_account_id": ""},
    {"medium": "physical"},
    {"medium": "unknown"},
    {"id": "numilog:public:other"},
])
def test_public_detail_and_local_refinement_refuse_foreign_scope(changes):
    original = page()
    row = replace(original.items[0], **changes)
    with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
        validate_public_detail_item(row)
    with pytest.raises(ProviderCapabilityError, match="unverified_local_page"):
        refine_loaded_public_page(replace(original, items=(row, *original.items[1:])),
                                  title="jefferson")


def test_structural_lookalike_is_not_a_native_result():
    original = page()
    row = NS(**vars(original.items[0]))
    with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
        validate_public_detail_item(row)
    with pytest.raises(ProviderCapabilityError, match="unverified_local_page"):
        refine_loaded_public_page(replace(original, items=(row, *original.items[1:])),
                                  title="jefferson")


def test_observed_anonymous_page_preserves_exact_identity_and_coverage():
    original = page()
    for row in original.items:
        assert validate_public_detail_item(row) == row.friendly_url
        assert row.source_account_id is None and row.medium == "digital"
    refined = refine_loaded_public_page(original, title="jefferson")
    assert refined.loaded_count == 20
    assert refined.public_unfiltered_total == 242
    assert refined.has_uninspected_or_unverified_matches
