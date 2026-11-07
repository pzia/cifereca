"""P1 #4761: completed recalculation must honour retained local view filters.

No GTK dependency, no network or provider refresh and no ranking mutation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from recommendation_service import RecommendationResult
from recommendations import UxRecommendationsView


class Label:
    def __init__(self):
        self.value = ""

    def set_label(self, value):
        self.value = value

    def get_label(self):
        return self.value


def prepared_view():
    view = object.__new__(UxRecommendationsView)
    view._generation = 7
    view._busy = True
    view._reference_book = None
    view._summary = Label()
    view._refresh_button = NS(set_sensitive=lambda *_: None)
    view._spinner = NS(stop=lambda: None)
    view._progress = NS(set_fraction=lambda *_: None, set_text=lambda *_: None)
    view._set_provisional = lambda *_: None
    view._reload_providers = lambda *_: None
    view._reload_languages = lambda *_: None
    view._reload_document_choices = lambda: None
    view._render = lambda preserve_summary=False: None
    view._master = NS(set_list_state=lambda *_: None)
    view._provider_labels = {"catalog": "Catalogue"}
    state = {"query": "", "field_labels": (), "format": None, "threshold": 15}
    view._score = NS(get_value=lambda: state["threshold"])
    view._affinity_initial_threshold = 15
    view._query_controls = NS(
        filter_text=lambda: state["query"],
        selected_sources=lambda: (),
        selected_languages=lambda: (),
        matches_document=lambda item: (
            state["format"] is None or item.get("format") == state["format"]
        ),
        active_document_labels=lambda: state["field_labels"],
    )
    return view, state


def finished(*, rows=True):
    ranked = (
        NS(item={"title": "Astronomie des mers", "author": "Alice",
                 "format": "EPUB"}, score=92, sources=("catalog",)),
        NS(item={"title": "Navigation des airs", "author": "Bob",
                 "format": "PDF"}, score=73, sources=("catalog",)),
    ) if rows else ()
    return RecommendationResult(
        books_count=5, queries=("astronomie",), ranked=ranked,
        providers=(("catalog", "Catalogue"),), cache_batches=1,
        network_batches=2,
    )


def test_unfiltered_recalculation_keeps_rich_generation_summary():
    view, _state = prepared_view()
    assert view._progress_complete(7, finished(), None) is False
    assert view._summary.get_label() == (
        "2 recommandations à partir de 5 livres et 1 signaux"
        " · cache 1 lot(s) · réseau 2 lot(s)"
    )


def test_recalculation_with_retained_query_updates_visible_counter():
    view, state = prepared_view()
    state["query"] = "astronomie"
    assert view._progress_complete(7, finished(), None) is False
    summary = view._summary.get_label()
    assert "1 sur 2" in summary
    assert "« astronomie »" in summary
    assert "Ordre diversifié" in summary
    assert "cache 1 lot(s) · réseau 2 lot(s)" in summary


def test_retained_document_facet_and_affinity_threshold_update_count():
    view, state = prepared_view()
    state["format"] = "EPUB"
    state["field_labels"] = ("EPUB",)
    view._base_complete(7, finished(), None)
    assert "1 sur 2 · EPUB · Ordre diversifié" == view._summary.get_label()

    state["format"] = None
    state["field_labels"] = ()
    state["threshold"] = 95
    view._base_complete(7, finished(), None)
    assert "0 sur 2 · Affinité ≥ 95 · Ordre diversifié" == view._summary.get_label()

    state["threshold"] = 15
    view._base_complete(7, finished(), None)
    assert view._summary.get_label().startswith(
        "2 recommandations à partir de 5 livres"
    )


def test_empty_recalculation_keeps_explanatory_zero_result_reason():
    view, state = prepared_view()
    state["query"] = "astronomie"
    view._base_complete(7, finished(rows=False), None)
    assert view._summary.get_label() == (
        "Aucune recommandation trouvée avec les catalogues actuellement connectés."
    )
