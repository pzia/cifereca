"""P0 #1129 — Library and Recent page builders: headless GTK behavior."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.library_page_controller import build_library_page, build_recent_page


OWNER = Path(__file__).resolve().parents[1] / "src/cifereca/gui/library_page_controller.py"
MAIN = OWNER.with_name("main_window.py")


class Widget:
    def __init__(self, *, label=None, icon=None, **kwargs):
        self.label = label
        self.icon = icon
        self.children = []
        self.handlers = {}
        self.popover = None
        self.tooltip = None

    def append(self, item):
        self.children.append(item)

    def connect(self, name, callback):
        self.handlers[name] = callback

    def click(self):
        return self.handlers["clicked"](self)

    def set_tooltip_text(self, text):
        self.tooltip = text

    def set_child(self, child):
        self.child = child

    def set_popover(self, popover):
        self.popover = popover

    def set_margin_top(self, _value):
        pass

    set_margin_bottom = set_margin_start = set_margin_end = set_margin_top


class Button(Widget):
    @staticmethod
    def new_from_icon_name(name):
        return Button(icon=name)


def _gtk():
    return NS(
        Button=Button,
        MenuButton=Widget,
        Popover=Widget,
        Box=Widget,
        Orientation=NS(VERTICAL=1, HORIZONTAL=0),
    )


def _page(window, *args):
    result = NS(
        title=args[0],
        args=args,
        toolbar=Widget(),
        header_actions=Widget(),
        connect_selected=Mock(),
    )
    window.last_page = result
    return result


def _window():
    events = []
    controls = NS(entry=object(), primary=Widget())
    window = NS(
        _closed=False,
        _library_search_online=Mock(),
        _library_selected=Mock(),
        _recent_selected=Mock(),
        refresh_library=lambda *a, **kw: events.append(("rescan", a, kw)),
        refresh_recent=lambda *a, **kw: events.append(("recent", a, kw)),
        choose_acsm=lambda: events.append(("acsm",)),
        open_library_folder=lambda: events.append(("folder",)),
        navigate=lambda target: events.append(("navigate", target)),
        run_kobo_backup=lambda: events.append(("forbidden_backup",)),
        _render_library=lambda: events.append(("render",)),
    )
    states = []
    window._query_controls = lambda changed, committed, *, library: (
        states.append((changed, committed, library)) or controls
    )
    return window, controls, events, states


def test_gtk_independent_owner_and_stable_mainwindow_facades():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module in {"gi", "main_window"}
        for node in ast.walk(owner)
    )
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in main.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = {node.name: node for node in cls.body
               if isinstance(node, ast.FunctionDef)}
    for method, target in [
        ("_build_library_page", "build_library_page"),
        ("_build_recent_page", "build_recent_page"),
    ]:
        facade = methods[method]
        assert [a.arg for a in facade.args.args] == ["self"]
        assert "library_page_controller." + target in ast.unparse(facade)
        assert "Gtk=Gtk" in ast.unparse(facade)


def test_library_header_primary_search_and_maintenance_keep_action_order():
    window, controls, events, states = _window()
    page = build_library_page(window, Gtk=_gtk(), MasterDetailPage=_page)
    assert page.title == "Bibliothèque"
    assert page.args[3] == "Fichiers locaux, livres possédés et empruntés."
    assert window.library_filter == "all"
    assert window.library_controls is controls
    assert window.library_filter_entry is controls.entry
    assert page.toolbar.children == [controls]
    assert len(states) == 1 and states[0][2] is True
    states[0][0]()
    states[0][1]()
    assert events == [("render",), ("render",)]
    search = controls.primary.children[0]
    assert search.label == "Recherche"
    search.click()
    window._library_search_online.assert_called_once_with(search)

    assert [(x.icon, x.label) for x in page.header_actions.children] == [
        ("view-refresh-symbolic", None),
        ("folder-open-symbolic", None),
        (None, "Opérations…"),
    ]
    refresh, folder, advanced = page.header_actions.children
    assert refresh.tooltip == "Rescanner la bibliothèque"
    refresh.click()
    folder.click()
    assert events[-2][0] == "rescan"
    assert events[-2][1] == (True, refresh)
    assert events[-1] == ("folder",)
    assert advanced.popover is not None
    page.connect_selected.assert_called_once_with(window._library_selected)
    assert window._library_objects == {}


def test_library_advanced_actions_never_trigger_global_kobo_acquisition():
    window, controls, events, states = _window()
    page = build_library_page(window, Gtk=_gtk(), MasterDetailPage=_page)
    advanced = page.header_actions.children[2]
    covers, acsm, sync = advanced.popover.child.children
    assert [b.label for b in (covers, acsm, sync)] == [
        "Réinspecter les couvertures",
        "Importer un ACSM…",
        "Gérer la synchronisation Kobo…",
    ]
    covers.click()
    acsm.click()
    sync.click()
    assert events == [
        ("rescan", (True, covers), {"refresh_covers": True}),
        ("acsm",), ("navigate", "sync"),
    ]
    assert all("forbidden_backup" not in str(event) for event in events)


def test_recent_page_refresh_only_in_header_and_selection_stays_scoped():
    window, controls, events, states = _window()
    page = build_recent_page(window, Gtk=_gtk(), MasterDetailPage=_page)
    assert page.title == "Récupérations récentes"
    assert page.args[2] == "Aucune récupération récente"
    assert page.toolbar.children == []
    assert len(page.header_actions.children) == 1
    refresh = page.header_actions.children[0]
    assert refresh.icon == "view-refresh-symbolic"
    assert refresh.tooltip == "Actualiser les récupérations récentes"
    refresh.click()
    assert events == [("recent", (refresh,), {})]
    assert window._recent_objects == {}
    page.connect_selected.assert_called_once_with(window._recent_selected)
