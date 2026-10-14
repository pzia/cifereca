"""P0 #1051: real dashboard overview rendering through an injected GTK double."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.dashboard_render_controller import render_dashboard


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "dashboard_render_controller.py"
MAIN = ROOT / "main_window.py"


class Widget:
    def __init__(self, *, label=None, **kwargs):
        self.props = {"label": label, **kwargs}
        self.children = []
        self.events = []
        self.handlers = {}
        self.removed = []

    def append(self, widget):
        self.children.append(widget)

    def get_child(self):
        return self

    def connect(self, signal, handler):
        self.handlers[signal] = handler

    def click(self):
        self.handlers["clicked"](self)

    def set_label(self, label):
        self.props["label"] = label

    def set_hexpand(self, value):
        self.props["hexpand"] = value

    def set_wrap(self, value):
        self.props["wrap"] = value

    def set_tooltip_text(self, text):
        self.props["tooltip"] = text

    def set_margin_top(self, value):
        self.props["mt"] = value

    def set_margin_bottom(self, value):
        self.props["mb"] = value

    def set_margin_start(self, value):
        self.props["ms"] = value

    def set_margin_end(self, value):
        self.props["me"] = value

    def set_size_request(self, width, height):
        self.props["size"] = (width, height)


class Grid(Widget):
    def get_first_child(self):
        return self.children[0] if self.children else None

    def remove(self, child):
        self.removed.append(child)
        self.children.remove(child)

    def insert(self, child, index):
        assert index == -1
        self.children.append(child)


class Child:
    def __init__(self, following=None):
        self.following = following

    def get_next_sibling(self):
        return self.following


GTK = NS(
    Box=Widget, Button=Widget, Label=Widget,
    Orientation=NS(HORIZONTAL=1, VERTICAL=2),
)


def _card(identity="alice", **changes):
    row = {
        "identity_id": identity, "label": f"Nom {identity}",
        "sources": [{"label": "Paris", "connected": True}],
        "capabilities": [{"id": "search", "label": "Recherche"}],
        "sync": {},
        "statistics": {},
    }
    row.update(changes)
    return row


def _window(cards=None, *, existing=None):
    events, diagnostics, styled = [], [], []
    service = NS(dashboard_state=lambda: events.append("snapshot") or
                 (list(cards) if cards is not None else []))
    window = NS(
        _closed=False,
        service=service,
        dashboard_cards=Grid(),
        _refresh_statistics=lambda: events.append("statistics"),
        _overview_listeners=[],
        diagnostics=NS(add=lambda *args, **kw: diagnostics.append((args, kw))),
        open_identity_library=lambda identity: events.append(("open", identity)),
        _sync_status_label=lambda status: "À jour" if status == "ok" else "À actualiser",
        _show_sync_issues=lambda row: events.append(("issues", row)),
    )
    window.dashboard_cards.children = list(existing or [])
    def style(widget, *classes):
        styled.append((widget, classes))
    def render():
        return render_dashboard(window, Gtk=GTK, add_css=style)
    return window, events, diagnostics, styled, render


def _descendant_labels(widget):
    labels = []
    if widget.props.get("label") is not None:
        labels.append(str(widget.props["label"]))
    for child in widget.children:
        labels.extend(_descendant_labels(child))
    return labels


def test_gtk_free_controller_keeps_mainwindow_facade_signature():
    module = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(module)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "main_window" in item for item in imports)
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    main = next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    facade = next(node for node in main.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_render_dashboard")
    assert [arg.arg for arg in facade.args.args] == ["self"]
    text = ast.unparse(facade)
    assert "dashboard_render_controller.render_dashboard" in text
    assert "Gtk=Gtk" in text and "add_css=add_css" in text


def test_closed_window_skips_listeners_service_and_widgets():
    window = NS(_closed=True)
    render_dashboard(window, Gtk=None, add_css=None)


def test_listeners_are_not_re_registered_and_work_without_cards_widget():
    events = []
    a, b = Mock(), Mock()
    window = NS(
        _overview_listeners=[a, b],
        _refresh_statistics=lambda: events.append("statistics"),
    )
    render_dashboard(window, Gtk=None, add_css=None)
    render_dashboard(window, Gtk=None, add_css=None)
    assert events == ["statistics", "statistics"]
    assert a.call_count == 2
    assert b.call_count == 2
    assert window._overview_listeners == [a, b]


def test_broken_plugin_observer_does_not_suppress_next_listener_or_cards():
    window, events, diagnostics, _styles, render = _window([_card()])
    def bad():
        events.append("bad")
        raise RuntimeError("private connection token")
    window._overview_listeners = [bad, lambda: events.append("good")]
    render()
    assert events[:4] == ["statistics", "bad", "good", "snapshot"]
    assert len(diagnostics) == 1
    args, kwargs = diagnostics[0]
    assert args == ("UI", "identity overview callback", "error")
    assert kwargs == {"detail": {"error": "RuntimeError"}}
    assert "private connection token" not in str(diagnostics)
    assert len(window.dashboard_cards.children) == 1


def test_old_cards_removed_before_new_identity_projection():
    first = Child()
    old_first = Child()
    old_second = Child()
    old_first.following = old_second
    window, events, _diagnostics, _styles, render = _window(
        [_card("alice"), _card("bob")],
        existing=[old_first, old_second],
    )
    render()
    assert window.dashboard_cards.removed == [old_first, old_second]
    assert len(window.dashboard_cards.children) == 2
    assert first not in window.dashboard_cards.children


def test_name_buttons_navigate_to_each_exact_identity_without_autoselection():
    window, events, _diagnostics, _styles, render = _window(
        [_card("a"), _card("b")],
    )
    render()
    name_buttons = [card.children[0].children[0]
                    for card in window.dashboard_cards.children]
    assert [button.props["label"] for button in name_buttons] == ["Nom a", "Nom b"]
    assert not any(isinstance(evt, tuple) and evt[0] == "open" for evt in events)
    name_buttons[1].click()
    name_buttons[0].click()
    assert events[-2:] == [("open", "b"), ("open", "a")]


def test_stats_provenance_and_derived_actions_preserve_counts_and_zero():
    card = _card(
        statistics={
            "local_books": 10,
            "known_loans": 0,
            "actions": {
                "acquisitions_succeeded": 2,
                "imports_succeeded": 3,
                "synchronizations_failed": 4,
                "transfers_failed": 1,
            },
        },
        sources=[
            {"label": "Paris", "connected": True},
            {"label": "Librairie", "connected": False},
        ],
        adept_profile_id="profile",
        capabilities=[{"id": "search", "label": "Recherche"}],
    )
    window, events, _diagnostics, styled, render = _window([card])
    render()
    labels = _descendant_labels(window.dashboard_cards.children[0])
    assert "10 livres locaux · 0 emprunts connus" in labels
    assert "Actions (30 j) : 5 réussies · 5 échouées" in labels
    assert "Sources : Paris, Librairie (à connecter)" in labels
    assert "Profil Adobe : Lié" in labels
    assert "✓ Recherche" in labels
    assert any("caption" in css for _widget, css in styled)
    assert ("open", "alice") not in events


def test_missing_optional_fields_keep_safe_ui_fallbacks():
    card = _card(label=None, sources=[], capabilities=[], statistics={},
                 deep_last_at="garbled-date")
    window, _events, _diagnostics, _styles, render = _window([card])
    render()
    labels = _descendant_labels(window.dashboard_cards.children[0])
    assert "Utilisateur" in labels
    assert "Sources : Aucune source connectée" in labels
    assert "Profil Adobe : À configurer" in labels
    assert "Dernière vérification complète : garbled-date" in labels


def test_sync_issues_require_explicit_click_and_keep_card_provenance():
    card = _card(
        sync={
            "status": "ok",
            "errors": 2,
            "attention_required": 1,
            "new_acquisitions": 7,
            "detail": {"adept_provisioning": {"state": "ready"}},
        },
    )
    window, events, _diagnostics, _styles, render = _window([card])
    render()
    labels = _descendant_labels(window.dashboard_cards.children[0])
    assert "À jour" in labels
    assert "Provisioning : ready" in labels
    assert "Nouvelles acquisitions : 7 · Actions : 1 · Erreurs : 2" in labels
    assert not any(isinstance(e, tuple) and e[0] == "issues" for e in events)
    issues_buttons = [
        node for node in window.dashboard_cards.children[0].children
        if node.props.get("label") == "Voir les erreurs et actions"
    ]
    assert len(issues_buttons) == 1
    issues_buttons[0].click()
    assert events[-1] == ("issues", card)


def test_no_sync_issues_no_maintenance_button():
    window, events, _diagnostics, _styles, render = _window([_card(sync={"status": "ok"})])
    render()
    labels = _descendant_labels(window.dashboard_cards.children[0])
    assert "Voir les erreurs et actions" not in labels
    assert not any(isinstance(e, tuple) and e[0] == "issues" for e in events)


def test_zero_cards_refreshes_stats_and_clears_old_cards_without_scan():
    window, events, _diagnostics, _styles, render = _window([], existing=[Child()])
    render()
    assert window.dashboard_cards.children == []
    assert events == ["statistics", "snapshot"]
