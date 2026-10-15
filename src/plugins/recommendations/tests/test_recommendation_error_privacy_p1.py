"""#1599: no provider exception payload in Recommendations public status or toasts.

Headless synthetic tasks, without network, GTK or actual account credentials.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from recommendations import UxRecommendationsView


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
    assert "PRIVATE" not in json.dumps((view._summary.value, toasts))
    assert view._base_complete(2, None, private_error) is False
    assert toasts == [view._summary.value]
