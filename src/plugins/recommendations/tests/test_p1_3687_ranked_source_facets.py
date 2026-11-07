"""#3687: a local Source facet lists only catalogues with ranked evidence.

Headless: never instantiate a provider, browse a network or infer an account
route from a connected provider or a malformed cached candidate.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from recommendation_service import RecommendationPartial, RecommendationResult
from recommendations import UxRecommendationsView


PROVIDERS = (("kobo", "Kobo"), ("paris", "Paris"), ("empty", "Catalogue sans résultat"))


class Facets:
    def __init__(self):
        self.sources = ()
        self.languages = ()
        self.calls = []

    def set_choices(self, *, sources=None, languages=None):
        if sources is not None:
            self.sources = tuple(sources)
            self.calls.append(("sources", self.sources))
        if languages is not None:
            self.languages = tuple(languages)
            self.calls.append(("languages", self.languages))


class Secret:
    def __str__(self):
        raise AssertionError("provider secrets must never be stringified")

    def __repr__(self):
        raise AssertionError("provider secrets must never be logged")


def ranked(source, title="Titre", *, language="fr"):
    return NS(item={"title": title, "author": "Autrice", "language": language},
              sources=(source,), score=80)


def view():
    instance = object.__new__(UxRecommendationsView)
    facets = Facets()
    instance._query_controls = facets
    instance._provider_labels = {"": "Tous les catalogues"}
    instance._generation = 5
    instance._busy = True
    instance._reference_book = None
    instance._set_provisional = lambda *_: None
    instance._refresh_button = NS(set_sensitive=lambda *_: None)
    instance._spinner = NS(stop=lambda: None)
    instance._reload_document_choices = lambda: None
    instance._render = lambda preserve_summary=False: None
    instance._update_view_summary = lambda **kwargs: None
    instance._show_calculation_unavailable = lambda message: None
    instance._master = NS(set_list_state=lambda *_: None)
    instance._summary = NS(set_label=lambda *_: None)
    instance.host = NS(toast=lambda *_: None)
    instance._progress = NS(
        set_fraction=lambda *_: None, set_text=lambda *_: None,
        set_tooltip_text=lambda *_: None,
    )
    instance._diagnostic_cache = 0
    instance._diagnostic_network = 0
    instance._provisional_order = {}
    return instance, facets


def test_source_choices_are_attested_by_ranked_candidates_not_connected_providers():
    instance, facets = view()
    instance._reload_providers(PROVIDERS, [ranked("paris")])
    assert facets.sources == (("paris", "Paris"),)
    assert "empty" not in instance._provider_labels

    # A second edition can be in several genuine source catalogues.
    instance._reload_providers(PROVIDERS, [NS(sources=("kobo", "paris"))])
    assert facets.sources == (("kobo", "Kobo"), ("paris", "Paris"))

    instance._reload_providers(PROVIDERS, [])
    assert facets.sources == ()


def test_result_only_filter_rejects_malformed_cache_and_unadvertised_sources():
    instance, facets = view()
    instance._reload_providers(
        PROVIDERS,
        [NS(sources=(Secret(), {"source_provider": "kobo"}, "paris")),
         NS(sources=Secret()), NS(sources=("other-catalogue",))],
    )
    assert facets.sources == (("paris", "Paris"),)

    # Without an explicit ranked set, old host callers still get inventory.
    instance._reload_providers(PROVIDERS)
    assert facets.sources == PROVIDERS


def test_completed_calculation_and_empty_result_replace_stale_source_facet():
    instance, facets = view()
    result = RecommendationResult(
        books_count=1, queries=("signal",),
        ranked=(ranked("kobo"),),
        providers=PROVIDERS,
    )
    assert instance._base_complete(5, result, None) is False
    assert facets.sources == (("kobo", "Kobo"),)
    assert facets.languages == (("fr", "Français"),)

    empty = RecommendationResult(
        books_count=1, queries=("signal",), ranked=(), providers=PROVIDERS,
    )
    assert instance._base_complete(5, empty, None) is False
    assert facets.sources == ()
    assert facets.languages == ()


def test_calculation_error_revokes_source_and_language_facets():
    instance, facets = view()
    instance._reload_providers(PROVIDERS, [ranked("kobo")])
    instance._reload_languages([ranked("kobo")])
    instance._ranked = [ranked("kobo")]
    assert facets.sources and facets.languages

    assert instance._base_complete(5, None, "calculation_failed") is False
    assert instance._ranked == []
    assert facets.sources == () and facets.languages == ()


def test_partial_refresh_projects_only_sources_on_current_partial_head():
    instance, facets = view()
    instance._ranked = [ranked("paris", title="Ancien")]
    instance._reload_providers(PROVIDERS, instance._ranked)
    instance._pending_partial = RecommendationPartial(
        ranked=(ranked("kobo", title="Nouveau"),),
        query_index=1, query_total=2, provider_done=1, provider_total=3,
        results_seen=1, cache_batches=1, network_batches=0,
        providers=PROVIDERS,
    )
    instance._partial_source_id = 3

    assert instance._flush_partial(5) is False
    assert [row.item["title"] for row in instance._ranked] == ["Nouveau"]
    assert facets.sources == (("kobo", "Kobo"),)
    assert facets.languages == (("fr", "Français"),)
    assert "Paris" not in repr(facets.sources)
