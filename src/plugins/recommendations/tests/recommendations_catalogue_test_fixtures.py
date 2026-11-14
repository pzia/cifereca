"""Synthetic fixtures exported by the Recommendations plugin tests (#5376)."""
from __future__ import annotations

from types import SimpleNamespace as NS
from cifereca.plugin_api import UiBookListEntry
from recommendation_engine import CatalogNoticeVariant, RankedCandidate
from recommendations import UxRecommendationsView


ISBN_1 = "9780306406157"
ISBN_2 = "9780140328721"


def _variant(isbn=ISBN_1, *, provider="kobo", credential="vault-1", record="book-1"):
    return CatalogNoticeVariant(
        item=NS(title="Une édition"),
        source_provider=provider,
        source_credential=credential,
        record_id=record,
        docbase="BOOKS",
        source_collection="catalogue",
        isbn=isbn,
    )


def _view(notices, *, isbn=None):
    recorded = []
    selection = ["reader-1"]
    rendered = []
    key = "book-1"
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
    view._generation = 3
    view._provisional_active = False
    view._provider_labels = {"kobo": "Kobo", "paris": "Paris"}
    item = NS(
        title="Une édition", author="Auteur", format="EPUB",
        published_at=None, description="Résumé public", friendly_url=None,
        thumbnail_url=None, cover_url=None,
    )
    view._ranked_by_key = {
        key: RankedCandidate(
            item=item, score=80, reasons=("Sujet proche",),
            sources=("kobo",), notices=tuple(notices),
        ),
    }
    view.selected_identity_id = lambda: selection[0]
    view.host = NS(search_books=lambda *a, **kw: recorded.append((a, kw)) or True)
    view._master = NS(
        show_detail=lambda detail: rendered.append(detail),
        empty_detail=lambda *_: None,
    )
    row = UiBookListEntry(key=key, title="Une édition")
    view._selected_row(row)
    return view, selection, recorded, rendered[0], row


