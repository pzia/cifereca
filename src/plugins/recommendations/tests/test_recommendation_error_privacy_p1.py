"""#1599: no provider exception payload in Recommendations public status or toasts.

Headless synthetic tasks, without network, GTK or actual account credentials.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from recommendations import UxRecommendationsView
from recommendation_service import RecommendationResult


class Label:
    def __init__(self):
        self.value = ""

    def set_label(self, value):
        self.value = value


class Task:
    control = NS(cancelled=lambda: False)

    def result(self):
        class PRIVATE_BEARER_TOKEN_Exception(RuntimeError):
            pass
        raise PRIVATE_BEARER_TOKEN_Exception(
            "https://secret.example/books?token=PRIVATE_ACCESS_TOKEN "
            "/home/PRIVATE_ACCOUNT/acquisition.acsm"
        )

    def add_done_callback(self, callback):
        callback(self)


def test_failed_worker_never_transfers_exception_or_class_to_glib():
    idle_calls = []
    view = object.__new__(UxRecommendationsView)
    view._task_handle = None
    view._reset_llm_selection = lambda key: None
    view.selected_identity_id = lambda: "profile-1"
    view._generation = 0
    view._reference_book = None
    view._refresh_button = NS(set_sensitive=lambda value: None)
    view._spinner = NS(start=lambda: None)
    view._progress = NS(set_fraction=lambda value: None, set_text=lambda value: None)
    view._summary = Label()
    view._exploration = NS(get_value=lambda: 25)
    view._exclude_owned = NS(get_active=lambda: True)
    view.GLib = NS(idle_add=lambda callback, *args: idle_calls.append((callback, args)))
    tasks = NS(submit=lambda operation, **kwargs: Task())
    view.plugin = NS(context=NS(broker=lambda name: tasks if name == "tasks" else object()))

    view._start_refresh()

    assert len(idle_calls) == 1
    callback, (generation, result, error) = idle_calls[0]
    assert callback == view._complete
    assert generation == view._generation == 1
    assert result is None
    assert error == "calculation_failed"
    assert "PRIVATE" not in repr(idle_calls)


def test_error_status_and_toast_redact_even_untrusted_explicit_error():
    view = object.__new__(UxRecommendationsView)
    view._generation = 3
    view._busy = True
    view._ranked = ["previous book"]
    view._refresh_button = NS(set_sensitive=lambda value: None)
    view._spinner = NS(stop=lambda: None)
    view._summary = Label()
    view._render = lambda: None
    states = []
    view._master = NS(set_list_state=lambda *args: states.append(args), empty_detail=lambda *_: None)
    toasts = []
    view.host = NS(toast=toasts.append)
    private_error = (
        "Exception in /home/PRIVATE_ACCOUNT/file.epub?token=PRIVATE_ACCESS_TOKEN"
    )

    assert view._base_complete(3, None, private_error) is False
    assert not view._busy
    assert view._ranked == []
    assert view._summary.value == (
        "Calcul impossible. Réessayez ou vérifiez les catalogues connectés."
    )
    assert toasts == [view._summary.value]
    assert states == [("Recommandations indisponibles", view._summary.value, "dialog-warning-symbolic")]
    assert "PRIVATE" not in json.dumps((view._summary.value, toasts))
    assert view._base_complete(2, None, private_error) is False
    assert toasts == [view._summary.value]


def test_empty_success_replaces_loading_message_with_the_actual_reason():
    view = object.__new__(UxRecommendationsView)
    view._generation = 5
    view._busy = True
    view._refresh_button = NS(set_sensitive=lambda _enabled: None)
    view._spinner = NS(stop=lambda: None)
    view._summary = Label()
    view._reference_book = None
    view._reload_providers = lambda _providers: None
    view._reload_languages = lambda _rows: None
    view._render = lambda **_options: None
    states = []
    view._master = NS(set_list_state=lambda *args: states.append(args))

    result = RecommendationResult(books_count=0, queries=(), ranked=(), providers=())
    assert view._base_complete(5, result, None) is False
    assert not view._busy
    assert states == [("Aucune recommandation", "Le profil ne contient aucun livre exploitable.")]


def test_final_partial_warning_remains_visible_after_filtering():
    view = object.__new__(UxRecommendationsView)
    view._generation = 6
    view._refresh_button = NS(set_sensitive=lambda _enabled: None)
    view._spinner = NS(stop=lambda: None)
    view._summary = Label()
    view._reference_book = None
    view._reload_providers = lambda _providers: None
    view._reload_languages = lambda _rows: None
    view._render = lambda **_options: None
    view._matches = lambda _row: False
    view._view_filters = lambda: ("Dumas", (), ())
    result = RecommendationResult(
        books_count=1, queries=("Dumas",), ranked=(object(),),
        providers=(("catalogue", "Catalogue"),), warnings=("provider error",),
    )
    view._base_complete(6, result, None)
    view._update_view_summary(provisional=False)
    assert view._summary.value == (
        "0 sur 1 · « Dumas » · Ordre diversifié · 1 recherche(s) partiellement en erreur"
    )


def test_summary_reports_active_shared_support_and_format_without_recalculation():
    view = object.__new__(UxRecommendationsView)
    view._ranked = ['one', 'two']
    view._matches = lambda row: row == 'one'
    view._view_filters = lambda: ('', (), ())
    view._summary = Label()
    view._partial_warning_count = 0
    view._query_controls = NS(
        active_document_labels=lambda: ('Type · Numérique', 'EPUB'),
    )
    view._update_view_summary(provisional=False)
    assert view._summary.value == (
        '1 sur 2 · Type · Numérique · EPUB · Ordre diversifié'
    )


def test_optional_document_labels_are_bounded_and_legacy_host_is_safe():
    view = object.__new__(UxRecommendationsView)
    view._ranked = ['one']
    view._matches = lambda row: True
    view._view_filters = lambda: ('', (), ())
    view._summary = Label()
    view._partial_warning_count = 0
    view._query_controls = NS(
        active_document_labels=lambda: ('SECRET' * 200,),
    )
    view._update_view_summary(provisional=False)
    assert view._summary.value == '1 recommandation(s) · Ordre diversifié'
    view._query_controls = None
    view._update_view_summary(provisional=False)
    assert view._summary.value == '1 recommandation(s) · Ordre diversifié'
