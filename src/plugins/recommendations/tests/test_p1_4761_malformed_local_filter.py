"""#4761: corrupt local filter state fails closed without leaking provider data.

Headless: no GTK display, network, LLM, actions or scoring recomputation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from recommendations import UxRecommendationsView


class Secret:
    def __str__(self):
        raise AssertionError("PRIVATE_BEARER")

    def __repr__(self):
        raise AssertionError("PRIVATE_BEARER")


class Label:
    def __init__(self):
        self.value = ""

    def set_label(self, text):
        self.value = text


def view(*, text="", sources=(), languages=(), field="all", document=True):
    instance = object.__new__(UxRecommendationsView)
    instance._score = NS(get_value=lambda: 0)
    instance._query_controls = NS(
        filter_text=lambda: text,
        selected_sources=lambda: sources,
        selected_languages=lambda: languages,
        filter_field=lambda: field,
        matches_document=lambda _item: document,
    )
    instance._summary = Label()
    instance._partial_warning_count = 0
    instance._provider_labels = {"kobo": "Kobo"}
    instance._ranked = []
    return instance


def item(*, source="kobo", language="fr"):
    return NS(
        score=85,
        sources=(source,),
        item={"title": "Titre fiable", "author": "Alice",
              "language": language, "format": "EPUB"},
    )


@pytest.mark.parametrize("kwargs", [
    {"sources": [[Secret()]]},
    {"sources": {"PRIVATE_KEY": "kobo"}},
    {"sources": ("kobo", Secret())},
    {"sources": ("kobo ",)},
    {"languages": [{"lang": "fr"}]},
    {"languages": ("fr", Secret())},
    {"languages": ("fr" * 100,)},
    {"text": Secret()},
    {"text": "\nPRIVATE_TOKEN"},
    {"text": "a" * 2049},
])
def test_corrupted_view_filter_never_matches_any_catalogue(kwargs):
    instance = view(**kwargs)
    assert instance._matches(item()) is False
    instance._ranked = [item()]
    instance._update_view_summary(provisional=False)
    assert instance._summary.value == (
        "0 sur 1 · Filtres locaux indisponibles · Ordre diversifié"
    )


def test_exception_from_filter_adapter_or_document_facets_fails_closed():
    instance = view()
    instance._query_controls.selected_sources = lambda: (_ for _ in ()).throw(
        RuntimeError("PRIVATE_TOKEN")
    )
    assert not instance._matches(item())
    instance._ranked = [item()]
    instance._update_view_summary(provisional=False)
    assert "PRIVATE" not in instance._summary.value

    instance = view()
    instance._query_controls.matches_document = lambda _item: (_ for _ in ()).throw(
        RuntimeError("PRIVATE_TOKEN")
    )
    assert not instance._matches(item())

    instance = view(text="titre")
    instance._query_controls.filter_field = lambda: (_ for _ in ()).throw(
        RuntimeError("PRIVATE_TOKEN")
    )
    assert not instance._matches(item())


def test_invalid_selected_field_object_never_enters_mapping_lookup():
    instance = view(text="titre", field=["PRIVATE_BEARER"])
    assert not instance._matches(item())
    instance._query_controls.filter_field = lambda: Secret()
    assert not instance._matches(item())


def test_valid_facets_preserve_local_filter_semantics():
    instance = view(
        text="titre", sources=("kobo",), languages=("fr",),
        field="title",
    )
    assert instance._matches(item())
    assert not instance._matches(item(source="paris"))
    assert not instance._matches(item(language="en"))


def test_render_with_invalid_source_revokes_stale_selection_and_actions():
    instance = view(sources=[[Secret()]])
    instance._ranked = [item()]
    instance._ranked_by_key = {"old": instance._ranked[0]}
    instance._exact_notice_state = {"key": "old"}
    instance._detail_selection_generation = 7
    instance._provisional_active = False
    instance._reset_llm_selection = lambda _key: None
    shown, states, cleared = [], [], []
    instance._master = NS(
        show_rows=lambda rows, **kwargs: shown.append(rows),
        set_list_state=lambda *args: states.append(args),
        empty_detail=lambda *args: cleared.append(args),
    )

    instance._render()
    assert shown == [()]
    assert instance._ranked_by_key == {}
    assert instance._exact_notice_state is None
    assert instance._detail_selection_generation == 8
    assert len(states) == len(cleared) == 1
    assert "Filtres locaux indisponibles" in instance._summary.value
