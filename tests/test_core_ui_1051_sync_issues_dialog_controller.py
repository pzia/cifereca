"""P0 #1051: sync issue dialog evidence, account scope and GTK lifetime."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.sync_issues_dialog_controller import show_sync_issues
from cifereca.gui.sync_issues import sync_issues_text


GUI = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = GUI / "main_window.py"
OWNER = GUI / "sync_issues_dialog_controller.py"


class Dialog:
    def __init__(self, parent, title, body, events):
        self.parent, self.title, self.body = parent, title, body
        self.responses = []
        self.events = events
        self.handlers = {}

    def add_response(self, code, label):
        self.responses.append((code, label))
        self.events.append(("response", code, label))

    def connect(self, signal, callback):
        self.handlers[signal] = callback
        self.events.append(("connect", signal))

    def present(self):
        self.events.append(("present",))

    def respond(self, choice):
        self.handlers["response"](self, choice)


def _host(*, with_generation=True):
    events, dialogs = [], []

    def new_dialog(owner, title, body):
        events.append(("new", title, body))
        dialog = Dialog(owner, title, body, events)
        dialogs.append(dialog)
        return dialog

    window = NS(
        _closed=False,
        navigate=lambda target: events.append(("navigate", target)),
        _settings_tabs=NS(
            set_visible_child_name=lambda page: events.append(("settings-tab", page))
        ),
    )
    if with_generation:
        window._identity_context_generation = 7
        window._account_context_generation = 12
    adw = NS(MessageDialog=NS(new=new_dialog))
    return window, adw, events, dialogs


def test_module_is_gtk_free_and_mainwindow_facade_preserves_signature():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(owner)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in entry or "main_window" in entry
                   for entry in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in main.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    facade = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_show_sync_issues")
    assert [arg.arg for arg in facade.args.args] == ["self", "card"]
    assert "sync_issues_dialog_controller.show_sync_issues" in ast.unparse(facade)
    assert "Adw=Adw" in ast.unparse(facade)


def test_report_title_and_exact_original_body_are_preserved():
    window, adw, events, dialogs = _host()
    card = {
        "label": "Compte Paris",
        "sync": {
            "detail": {
                "acquisition_errors": [
                    {
                        "title": "Édition A",
                        "status": "retryable_error",
                        "error": "Erreur https://example.test/?token=SECRET",
                    },
                ],
            },
        },
    }
    show_sync_issues(window, card, Adw=adw)
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.parent is window
    assert dialog.title == "Synchronisation · Compte Paris"
    assert dialog.body == sync_issues_text(card["sync"])
    assert "Édition A" in dialog.body
    assert "token=SECRET" in dialog.body
    assert dialog.responses == [
        ("settings", "Paramètres des identités"),
        ("close", "Fermer"),
    ]
    assert [e[0] for e in events] == [
        "new", "response", "response", "connect", "present",
    ]


def test_missing_sync_details_keep_explanatory_old_run_message():
    window, adw, events, dialogs = _host()
    show_sync_issues(window, {"label": None, "sync": None}, Adw=adw)
    assert dialogs[0].title == "Synchronisation · "
    assert "ancienne synchronisation" in dialogs[0].body
    assert not any(event[0] == "navigate" for event in events)


def test_report_does_not_refresh_scan_or_navigate_without_a_click():
    window, adw, events, dialogs = _host()
    show_sync_issues(window, {"sync": {}}, Adw=adw)
    assert len(dialogs) == 1
    assert not any(e[0] in {"navigate", "settings-tab"} for e in events)
    assert not hasattr(window, "service")
    assert not hasattr(window, "runner")


def test_explicit_settings_response_keeps_navigation_before_accounts_tab():
    window, adw, events, dialogs = _host()
    show_sync_issues(window, {"sync": {}}, Adw=adw)
    dialogs[0].respond("settings")
    assert events[-2:] == [
        ("navigate", "settings"),
        ("settings-tab", "accounts"),
    ]


@pytest.mark.parametrize("choice", ["close", "cancel", "", "unknown"])
def test_non_settings_response_cannot_navigate(choice):
    window, adw, events, dialogs = _host()
    show_sync_issues(window, {"sync": {}}, Adw=adw)
    dialogs[0].respond(choice)
    assert not any(e[0] in {"navigate", "settings-tab"} for e in events)


def test_window_closed_before_open_skips_card_and_adw_access():
    window = NS(_closed=True)
    class BombCard:
        def get(self, key):
            raise AssertionError("must not read a stale card")
    show_sync_issues(window, BombCard(), Adw=None)


def test_queued_settings_response_after_teardown_is_inert():
    window, adw, events, dialogs = _host()
    show_sync_issues(window, {"sync": {}}, Adw=adw)
    window._closed = True
    events.clear()
    dialogs[0].respond("settings")
    assert events == []


@pytest.mark.parametrize("dimension", [
    "_identity_context_generation", "_account_context_generation",
])
def test_stale_report_cannot_redirect_a_new_identity_or_account(dimension):
    window, adw, events, dialogs = _host()
    show_sync_issues(window, {"sync": {}}, Adw=adw)
    setattr(window, dimension, getattr(window, dimension) + 1)
    events.clear()
    dialogs[0].respond("settings")
    assert events == []


def test_compatible_with_hosts_without_generation_counters():
    window, adw, events, dialogs = _host(with_generation=False)
    show_sync_issues(window, {"sync": {}}, Adw=adw)
    dialogs[0].respond("settings")
    assert events[-2:] == [
        ("navigate", "settings"), ("settings-tab", "accounts"),
    ]


def test_no_incidental_mutation_of_sync_source_mapping():
    window, adw, events, dialogs = _host()
    sync = {"detail": {"provider_errors": [
        {"title": "Catalogue public", "error": "Connexion interrompue"}
    ]}}
    before = {"detail": {"provider_errors": [dict(sync["detail"]["provider_errors"][0])]}}
    show_sync_issues(window, {"label": "État", "sync": sync}, Adw=adw)
    assert sync == before
    assert dialogs[0].body == sync_issues_text(sync)


def test_second_dialog_captures_its_own_identity_generation():
    window, adw, events, dialogs = _host()
    show_sync_issues(window, {"label": "Alice", "sync": {}}, Adw=adw)
    window._identity_context_generation += 1
    show_sync_issues(window, {"label": "Bob", "sync": {}}, Adw=adw)
    events.clear()
    dialogs[0].respond("settings")
    assert events == []
    dialogs[1].respond("settings")
    assert events == [
        ("navigate", "settings"),
        ("settings-tab", "accounts"),
    ]
