"""#4761: provider discovery, observed responses and ranked sources differ.

All provider replies are synthetic. The classification is closed and never
includes private remote error messages or Search provenance rights.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from recommendation_service import (
    CatalogueSearchObservation,
    RecommendationResult,
    compute_recommendations,
)
from recommendations import (
    UxRecommendationsView,
    _catalogue_coverage_projection,
)


class Metadata:
    def library_entries(self, *, identity_id, scan):
        assert identity_id == "alice" and scan is False
        return [
            {"title": "Seed", "author": "Alice",
             "metadata": {"subjects": ["histoire"]}},
        ]


class Catalog:
    def searchable_providers(self):
        return (
            ("a", "Catalogue A"),
            ("b", "Catalogue B"),
            ("c", "Catalogue C"),
            ("d", "Catalogue D"),
        )

    def search_progressive(self, query, *, on_batch, **kwargs):
        assert kwargs["scope"] == "digital" and kwargs["force"] is False
        assert query
        for provider_id, rows, error in (
            ("a", (
                {"title": "Nouveau livre", "author": "Alice",
                 "source_provider": "a"},), ""),
            ("b", (
                {"title": "Texte sans lien", "author": "Autre",
                 "source_provider": "b"},), ""),
            ("c", (), ""),
            ("d", (), "https://PRIVATE_ACCOUNT.invalid/?token=SECRET"),
        ):
            on_batch(NS(
                provider_id=provider_id, results=rows,
                error_message=error, error_code="SearchUnavailable" if error else None,
                warnings=(), cached=False,
            ))
        return NS(cancelled=False, warnings=())


@pytest.fixture(scope="module")
def catalogue_result():
    # Both projection scenarios start from the same immutable synthetic result.
    return compute_recommendations(
        Metadata(), Catalog(), "alice",
        max_queries=1, exclude_owned=False, exploration=0,
    )


def test_catalogue_responses_are_distinct_from_ranking_and_empty_results(catalogue_result):
    result = catalogue_result
    assert not result.cancelled
    assert set(row.sources[0] for row in result.ranked) == {"a"}
    assert tuple(
        (row.provider_id, row.batches, row.results_seen, row.failures)
        for row in result.catalogue_observations
    ) == (
        ("a", 1, 1, 0), ("b", 1, 1, 0),
        ("c", 1, 0, 0), ("d", 1, 0, 1),
    )
    summary, detail = _catalogue_coverage_projection(result)
    assert "4 catalogue(s) disponibles" in summary
    assert "4 avec réponse" in summary
    assert "1 retenu(s)" in summary
    assert "1 hors classement" in summary
    assert "1 sans notice" in summary
    assert "1 en erreur" in summary
    assert "0 sans lot" in summary
    assert "Catalogue B : notices reçues, non retenues" in detail
    assert "Catalogue C : réponse sans notice" in detail
    assert "Catalogue D : erreur fournisseur" in detail
    assert "PRIVATE_ACCOUNT" not in summary + detail
    assert "SECRET" not in summary + detail


def test_unreported_provider_is_not_mislabelled_as_empty_and_sync_is_unknown(catalogue_result):
    result = catalogue_result
    observations = tuple(row for row in result.catalogue_observations
                         if row.provider_id != "c")
    degraded = replace(result, catalogue_observations=observations)
    summary, detail = _catalogue_coverage_projection(degraded)
    assert "1 sans lot" in summary
    assert "Catalogue C : aucun lot reçu" in detail

    # Old brokers lacking per-provider events cannot attest a response.
    legacy = replace(result, catalogue_observations=())
    summary, detail = _catalogue_coverage_projection(legacy)
    assert "réponses individuelles non détaillées" in summary
    assert detail == ""


class Secret:
    def __str__(self):
        raise AssertionError("do not stringify private provider objects")


def test_public_projection_ignores_malformed_sources_and_untrusted_observations():
    result = RecommendationResult(
        1, ("signal",), (),
        (("a", "Public"), ("a", "Duplicate"),
         ("unsafe" + chr(10), "PRIVATE\nTOKEN"), (Secret(), Secret())),
        catalogue_observations=(
            CatalogueSearchObservation("a", 1, 0, 0),
            CatalogueSearchObservation("a", 1, 12, 9),
            CatalogueSearchObservation("bad", 1, 1, 1),
            CatalogueSearchObservation("unsafe" + chr(10), 1, 1, 1),
            CatalogueSearchObservation("a", -3, 1, 0),
        ),
        warnings=("PRIVATE_ACCOUNT_TOKEN",),
    )
    summary, detail = _catalogue_coverage_projection(result)
    assert "1 catalogue(s)" in summary
    assert "1 sans notice" in summary
    assert "Public : réponse sans notice" in detail
    assert "PRIVATE" not in summary + detail


def test_coverage_label_can_be_cleared_without_reloading_ranked_source_filter():
    class Label:
        def __init__(self):
            self.text = None
            self.tooltip = None
            self.visible = None

        def set_label(self, value):
            self.text = value

        def set_tooltip_text(self, value):
            self.tooltip = value

        def set_visible(self, value):
            self.visible = value

    view = object.__new__(UxRecommendationsView)
    label = Label()
    view._catalogue_overview = label
    result = RecommendationResult(
        1, ("Alice",), (), (("a", "Catalogue A"),),
        catalogue_observations=(CatalogueSearchObservation("a", 1, 0, 0),),
    )
    view._set_catalogue_coverage(result)
    assert label.visible is True
    assert "sans notice" in label.text
    assert "Catalogue A" in label.tooltip

    view._set_catalogue_coverage()
    assert label.visible is False
    assert label.text == "" and label.tooltip is None
