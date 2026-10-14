from __future__ import annotations

import ast
from pathlib import Path

from cifereca.gui.shell_model import (
    action_registry,
    validate_action_selection,
    visible_actions,
)
from cifereca.plugin_api import UiActionContribution, UiPageContribution


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
HEADER_OWNER = ROOT / "src/cifereca/gui/header_action_controller.py"
PLUGIN_PAGE = ROOT / "src/cifereca/gui/plugin_manager_page.py"


def _action(action_id, title, *, global_action=False):
    return UiActionContribution(
        id=action_id,
        title=title,
        callback=lambda _host: None,
        global_action=global_action,
    )


def _pages():
    return [
        UiPageContribution(
            route="search",
            title="Recherche",
            order=20,
            factory=lambda _host: object(),
            actions=(_action("online", "Étendre en ligne"),),
        ),
        UiPageContribution(
            route="library",
            title="Bibliothèque",
            order=30,
            factory=lambda _host: object(),
            actions=(_action("import-acsm", "Importer ACSM", global_action=True),),
        ),
    ]


def test_action_registry_exposes_stable_keys_and_scope():
    rows = action_registry(_pages())

    assert [(row.key, row.scope) for row in rows] == [
        ("search.open", "global"),
        ("search.online", "page"),
        ("library.open", "global"),
        ("library.import-acsm", "global"),
    ]
    assert rows[1].page_title == "Recherche"
    assert rows[3].title == "Importer ACSM"


def test_selection_validation_filters_obsolete_and_duplicates_preserving_order():
    valid, obsolete = validate_action_selection(
        _pages(),
        ["library.import-acsm", "old.route", "search.open", "old.route", "search.open"],
    )

    assert valid == ("library.import-acsm", "search.open")
    assert obsolete == ("old.route",)


def test_visible_actions_never_renders_unknown_ids_and_respects_scope():
    rows = visible_actions(
        _pages(),
        "search",
        ["old.route", "library.import-acsm", "search.online", "library.open"],
    )

    assert [key for key, _action in rows] == [
        "search.online",
        "library.open",
        "library.import-acsm",
    ]


def test_plugin_configuration_surfaces_scope_and_obsolete_ids():
    source = PLUGIN_PAGE.read_text(encoding="utf-8")

    assert "action_registry" in source
    assert "validate_action_selection" in source
    assert 'scope = "Globale"' in source
    assert '"Page · {entry.page_title}"' in source
    assert '"Action obsolète : {key}"' in source


def _method_source(name: str) -> str:
    if name == "_plugins_changed":
        path = MAIN.parent / "plugin_reload_controller.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in tree.body
                                if isinstance(node, ast.FunctionDef)
                                and node.name == "plugins_changed"))
    if name == "_refresh_header_actions":
        tree = ast.parse(HEADER_OWNER.read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in tree.body
                                if isinstance(n, ast.FunctionDef)
                                and n.name == "refresh_header_actions"))
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_shell_filters_configured_actions_before_render():
    source = _method_source("_refresh_header_actions")

    assert "validate_action_selection" in source
    assert "obsolete_action_ids" in source
    assert "visible_actions(window._pages.values(), route, selected)" in source


def test_page_menu_is_reasserted_rightmost_after_build_refresh_and_reconstruction():
    create = _method_source("_create_shell_header")
    refresh = _method_source("_refresh_header_actions")
    changed = _method_source("_plugins_changed")

    assert "self._header_controls = controls" in create
    assert "self._ensure_pages_menu_rightmost()" in create
    assert "window._ensure_pages_menu_rightmost()" in refresh
    assert "window._ensure_pages_menu_rightmost()" in changed
    main = MAIN.read_text(encoding="utf-8")
    assert "plugin_reload_controller.plugins_changed(self)" in main


def test_rightmost_helper_moves_menu_to_last_position_for_headless_box():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_ensure_pages_menu_rightmost")
    module = ast.Module(body=[method], type_ignores=[])
    ns = {}
    exec(compile(ast.fix_missing_locations(module), str(MAIN), "exec"), ns)

    menu = object()
    controls = type("Box", (), {})()
    controls.children = [menu, "identity", "statistics"]
    host = type("Host", (), {"_header_controls": controls, "pages_menu_button": menu})()

    ns["_ensure_pages_menu_rightmost"](host)

    assert controls.children == ["identity", "statistics", menu]
