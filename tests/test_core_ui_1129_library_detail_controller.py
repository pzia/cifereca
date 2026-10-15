"""P0 #1129: Library detail selection, provenance and actions without GTK."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.library_detail_controller import library_selected


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "library_detail_controller.py"


class Button:
    instances = []

    def __init__(self, *, label):
        self.label = label
        self.handlers = {}
        self.css_classes = []
        Button.instances.append(self)

    def add_css_class(self, klass):
        self.css_classes.append(klass)

    def set_sensitive(self, value):
        self.sensitive = value

    def set_tooltip_text(self, title):
        self.tooltip = title

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def click(self):
        self.handlers["clicked"](self)


def _context(item=None, *, can_download=False, resolved_loan=None):
    events = []
    item = item or {"key": "book-1", "title": "Livre", "provider_id": "paris"}
    detail = NS(
        empty=lambda *args: events.append(("empty", args)),
        show_loan=lambda loan, **kwargs: events.append(("loan", loan, kwargs)),
        show_presentation=lambda presentation, **kwargs: events.append(
            ("presentation", presentation, kwargs)
        ),
        add_actions=lambda actions: events.append(("actions", actions)),
        add_group=lambda title, rows: events.append(("group", title, rows)),
    )
    identity_labels = {"alice": "Alice", "bob": "Bob"}
    service = NS(
        identity_by_id=lambda key: NS(label=identity_labels[key])
        if key in identity_labels else None,
        provider_display_name=lambda key: {"paris": "Paris", "kobo": "Kobo"}.get(key, key),
        can_download_loan=Mock(return_value=can_download),
    )
    lookup = Mock(return_value=resolved_loan)
    window = NS(
        _closed=False,
        _cancel_cached_book_details=lambda: events.append(("cancel",)),
        _library_objects={item["key"]: item},
        library_page=NS(detail=detail),
        library_controls=object(),
        loan_objects={"a": object(), "b": object()},
        service=service,
        _library_availability_label=lambda value: "Disponible",
        _kobo_backup_status_label=lambda value: "À jour",
        _book_description=lambda value: "Résumé",
        _add_book_notice=lambda panel, value, **opts: events.append(("notice", opts)),
        _add_local_book_actions=lambda panel, value: events.append(("local",)),
        _add_book_recommendation_action=lambda panel, value: events.append(("recommend",)),
        _add_facets=lambda panel, value, controls: events.append(("facets",)),
        _add_plugin_book_actions=lambda panel, value: events.append(("plugin", panel, value)),
        _request_cached_book_details=lambda route, key, value: events.append(
            ("cache", route, key)
        ),
        prepare_pnb_for_loan=lambda loan, automatic, button: events.append(
            ("prepare", loan, automatic, button)
        ),
    )
    projected = []
    def projection(**kwargs):
        projected.append(kwargs)
        return NS(**kwargs)

    def call(row):
        return library_selected(
            window, row, Gtk=NS(Button=Button),
            unique_library_loan=lookup,
            book_detail_presentation=projection,
            detail_language=lambda metadata: "fr",
            document_links=lambda value: ("https://example.invalid/notice",),
        )
    return window, events, projected, lookup, call


def test_no_gtk_import_and_stable_mainwindow_callback_signature():
    source = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(source)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(n == "import gi" or n.startswith("from gi ")
                   or "main_window" in n for n in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef)
               and n.name == "MainWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_library_selected")
    assert [a.arg for a in method.args.args] == ["self", "row"]
    facade = ast.unparse(method)
    assert "library_detail_controller.library_selected" in facade
    assert "unique_library_loan=unique_library_loan" in facade
    assert "book_detail_presentation=book_detail_presentation" in facade


def test_disposed_window_never_touches_gtk_service_or_cache():
    assert library_selected(
        NS(_closed=True), NS(key="book-1"), Gtk=NS(),
        unique_library_loan=None, book_detail_presentation=None,
        detail_language=None, document_links=None,
    ) is None


def test_no_selection_cancels_old_cache_then_renders_neutral_detail():
    window, events, presentations, lookup, call = _context()
    call(None)
    assert events == [
        ("cancel",),
        ("empty", ("Sélectionnez un livre",
                   "Sélectionnez un livre dans la liste pour afficher ses détails.")),
    ]
    assert presentations == []
    lookup.assert_not_called()


def test_missing_row_key_never_uses_stale_detail_or_refreshes_plugins():
    window, events, presentations, lookup, call = _context()
    call(NS(key="no-longer-visible"))
    assert events == [("cancel",)]
    assert presentations == []
    lookup.assert_not_called()


def test_multi_identity_provenance_is_deduplicated_and_metadata_is_preserved():
    item = {
        "key": "book-1", "title": "Deux sources", "author": "Auteur",
        "provider_id": "paris", "media_format": "epub",
        "identity_id": "alice", "local_path": "/tmp/example.epub",
        "sync_status": "up-to-date", "metadata": {"language": "fr"},
        "placements": [
            {"identity_id": "alice", "storage_target_id": "disk"},
            {"identity_id": "alice", "storage_target_id": "disk"},
        ],
        "possessions": [
            {"identity_id": "bob", "provider_id": "paris"},
            {"identity_id": "bob", "provider_id": "paris"},
        ],
    }
    window, events, projected, lookup, call = _context(item)
    call(NS(key="book-1"))
    assert events[0] == ("cancel",)
    assert events[1][0] == "presentation"
    assert events[1][2]["description"] == "Résumé"
    assert projected[0]["metadata"] == item["metadata"]
    assert projected[0]["links"] == ("https://example.invalid/notice",)
    assert projected[0]["badges"] == []
    sections = dict(projected[0]["section_rows"])
    assert ("Disponibilité", "Disponible") in sections["Essentiel"]
    assert ("Fichier local", "/tmp/example.epub") in sections["Bibliothèque"]
    assert ("État acquisition", "") in sections["Bibliothèque"]
    assert ("Identité", "Alice") in sections["Bibliothèque"]
    assert events[2] == (
        "group", "Présence / Bibliothèques / Sources",
        [("Alice", "Stockage : disk"), ("Bob", "Droit distant : Paris")],
    )
    assert [entry[0] for entry in events[3:]] == [
        "notice", "local", "recommend", "facets", "plugin", "cache",
    ]
    assert events[-1] == ("cache", "library", "book-1")
    window.service.can_download_loan.assert_not_called()


def test_exact_loan_adds_download_action_only_for_matched_loan():
    loan = object()
    item = {"key": "book-1", "source": "loan", "loan_id": "loan-1"}
    window, events, projected, lookup, call = _context(
        item, can_download=True, resolved_loan=loan,
    )
    Button.instances.clear()
    call(NS(key="book-1"))
    lookup.assert_called_once()
    args = lookup.call_args.args
    assert len(args[0]) == 2
    assert args[1] is item
    assert events[1] == ("loan", loan, {"include_pnb_action": False})
    assert len(Button.instances) == 1
    button = Button.instances[0]
    assert button.label == "Télécharger le livre du prêt"
    assert button.css_classes == ["suggested-action"]
    assert events[2] == ("actions", [button])
    assert [entry[0] for entry in events[3:]] == [
        "facets", "local", "recommend", "cache",
    ]
    assert projected == []
    button.click()
    assert events[-1] == ("prepare", loan, False, button)


def test_ambiguous_loan_uses_neutral_catalogue_detail_not_wrong_borrower():
    item = {"key": "book-1", "source": "loan", "loan_id": "shared"}
    window, events, projected, lookup, call = _context(item, resolved_loan=None)
    call(NS(key="book-1"))
    lookup.assert_called_once()
    assert all(entry[0] != "loan" for entry in events)
    assert all(entry[0] != "prepare" for entry in events)
    assert [entry[0] for entry in events] == [
        "cancel", "presentation", "notice", "local", "recommend", "facets", "plugin", "cache",
    ]
    window.service.can_download_loan.assert_not_called()


def test_loan_without_download_right_still_renders_facets_and_recommendation():
    loan = object()
    item = {"key": "book-1", "source": "loan", "loan_id": "loan-1"}
    window, events, projected, lookup, call = _context(
        item, can_download=False, resolved_loan=loan,
    )
    Button.instances.clear()
    call(NS(key="book-1"))
    assert events[1] == ("loan", loan, {"include_pnb_action": False})
    assert events[2] == ("actions", [])
    assert Button.instances == []
    assert [entry[0] for entry in events[3:]] == [
        "facets", "local", "recommend", "cache",
    ]


def test_standard_library_detail_exposes_real_public_plugin_contribution():
    from cifereca.gui.plugin_book_action_controller import add_plugin_book_actions
    from cifereca.gui.shell_model import contextual_actions
    from cifereca.plugin_api import BookRef, UiActionContext, UiActionContribution, UiPageContribution
    item = {"key": "book-1", "item_id": "source-a", "title": "A"}
    window, events, projected, lookup, call = _context(item)
    activations = []
    action = UiActionContribution(
        "test.explore", "Explorer ce livre", lambda host: activations.append(host.action_context()),
        target="book.detail", visible_when=lambda context: context.book is not None,
    )
    context = UiActionContext(target="book.detail", route="library", book=BookRef("source-a", "A"))
    window._pages = {"explore": UiPageContribution("explore", "Explorer", "", 30, lambda host: None, actions=(action,))}
    window._book_action_context = lambda row: context
    window.action_button = lambda label, icon: Button(label=label)
    window._UiHost = lambda owner, selected: NS(action_context=lambda: selected)
    window._invoke_ui_action = lambda selected, host: selected.callback(host)
    window._add_plugin_book_actions = lambda panel, row: add_plugin_book_actions(
        window, panel, row, contextual_actions=contextual_actions)
    Button.instances.clear()
    call(NS(key="book-1"))
    buttons = [button for button in Button.instances if button.label == "Explorer ce livre"]
    assert len(buttons) == 1
    assert activations == []  # rendering never invokes the plugin
    buttons[0].click()
    assert activations == [context]
    assert activations[0].book.book_id == "source-a"
    # Once the contribution is removed, the old button is inert.
    window._pages.clear()
    buttons[0].click()
    assert activations == [context]
