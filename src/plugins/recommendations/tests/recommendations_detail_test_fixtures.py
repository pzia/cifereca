"""Synthetic fixtures exported by the Recommendations plugin tests (#5376)."""
from __future__ import annotations

from types import SimpleNamespace as NS
from recommendation_engine import RankedCandidate
from recommendations import UxRecommendationsView


def _make_view(item):
    view = object.__new__(UxRecommendationsView)
    view._llm_generation = 0
    view._llm_selection_key = None
    view._llm_handle = None
    view._llm_selected_row = None
    view._llm_explanation = None
    view._llm_enabled = False
    view._llm_button = None
    view._llm_hint = None
    view._busy = False
    view._generation = 1
    view._provisional_active = False
    view._provider_labels = {"kobo": "Kobo"}
    view._ranked = [RankedCandidate(
        item=item, score=92, reasons=("Auteur commun",),
        sources=("kobo",),
    )]
    view._matches = lambda _ranked: True
    view._candidate_key = lambda _ranked: "ed1"
    view.selected_identity_id = lambda: "reader-1"
    rows, details, empties = [], [], []
    view._master = NS(
        show_rows=lambda entries, **kwargs: rows.extend(entries),
        show_detail=details.append,
        empty_detail=lambda *args: empties.append(args),
    )
    view.host = NS(search_books=lambda *a, **kw: True)
    return view, rows, details, empties


