"""#3687/#4761: an incomplete ranking cannot silently reset an active source.

No GTK runtime, no provider I/O, no exact Search/borrow/download action.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from recommendation_service import RecommendationPartial, RecommendationResult
from recommendations import UxRecommendationsView


PROVIDERS = (("kobo", "Kobo"), ("paris", "Paris"), ("empty", "Sans résultat"))


def ranked(source, *, title="Livre"):
    return NS(
        item={"title": title, "author": "Autrice", "language": "fr"},
        sources=(source,), score=83,
    )


class Facets:
    """Model the real MultiSelect contract: dropping a choice drops its tick."""

    def __init__(self, selected=("paris",)):
        self._selected = tuple(selected)
        self.choices = ()
        self.updates = []

    def set_choices(self, *, sources=None, languages=None):
        if sources is not None:
            self.choices = tuple(sources)
            allowed = {key for key, _label in sources}
            self._selected = tuple(value for value in self._selected if value in allowed)
            self.updates.append(self.choices)

    def selected_sources(self):
        return self._selected

    def selected_languages(self):
        return ()

    def filter_text(self):
        return ""

    def matches_document(self, _item):
        return True


def view(selected=("paris",)):
    instance = object.__new__(UxRecommendationsView)
    facets = Facets(selected)
    instance._query_controls = facets
    instance._provider_labels = {"": "Tous les catalogues"}
    instance._generation = 5
    instance._busy = True
    instance._reference_book = None
    instance._ranked = [ranked("paris", title="Précédent")]
    instance._score = NS(get_value=lambda: 0)
    instance._set_provisional = lambda *_: None
    instance._refresh_button = NS(set_sensitive=lambda *_: None)
    instance._spinner = NS(stop=lambda: None)
    instance._reload_document_choices = lambda: None
    instance._render = lambda preserve_summary=False: None
    instance._update_view_summary = lambda **_kw: None
    instance._master = NS(set_list_state=lambda *_: None)
    instance._summary = NS(set_label=lambda *_: None)
    instance._progress = NS(
        set_fraction=lambda *_: None, set_text=lambda *_: None,
        set_tooltip_text=lambda *_: None,
    )
    instance._diagnostic_cache = 0
    instance._diagnostic_network = 0
    instance._provisional_order = {}
    return instance, facets


def test_selected_source_stays_active_until_final_result_not_first_partial_head():
    instance, facets = view()
    instance._reload_providers(PROVIDERS)
    assert facets.selected_sources() == ("paris",)
    assert not instance._matches(ranked("kobo"))

    instance._pending_partial = RecommendationPartial(
        ranked=(ranked("kobo", title="Nouveau"),),
        query_index=1, query_total=2, provider_done=1, provider_total=3,
        results_seen=1, cache_batches=1, network_batches=0,
        providers=PROVIDERS,
    )
    instance._partial_source_id = 1
    assert instance._flush_partial(5) is False
    assert facets.choices == (("kobo", "Kobo"), ("paris", "Paris"))
    assert facets.selected_sources() == ("paris",)
    assert not instance._matches(instance._ranked[0])
    assert ("empty", "Sans résultat") not in facets.choices

    final = RecommendationResult(
        books_count=1, queries=("signal",), ranked=(ranked("kobo"),),
        providers=PROVIDERS,
    )
    assert instance._base_complete(5, final, None) is False
    assert facets.choices == (("kobo", "Kobo"),)
    assert facets.selected_sources() == ()
    assert instance._matches(instance._ranked[0])


def test_disconnected_source_is_not_preserved_even_during_partial_calculation():
    instance, facets = view(("missing",))
    instance._ranked = [ranked("kobo")]
    instance._reload_providers(PROVIDERS)
    assert facets.choices == (("kobo", "Kobo"),)
    assert facets.selected_sources() == ()


def test_normal_unselected_partial_displays_only_sources_in_current_ranking():
    instance, facets = view(())
    instance._ranked = [ranked("kobo")]
    instance._reload_providers(PROVIDERS)
    assert facets.choices == (("kobo", "Kobo"),)
    assert facets.selected_sources() == ()


def test_not_busy_never_preserves_stale_selection_even_with_ranked_rows():
    instance, facets = view()
    instance._busy = False
    instance._ranked = [ranked("kobo")]
    instance._reload_providers(PROVIDERS)
    assert facets.choices == (("kobo", "Kobo"),)
    assert facets.selected_sources() == ()
