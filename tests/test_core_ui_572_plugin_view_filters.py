from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.plugin_view import (
    PLUGIN_VIEW_FILTERS,
    filter_package_plugins,
    package_matches_query,
)


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/cifereca/gui/plugin_manager_page.py"


def _status(
    plugin_id: str,
    *,
    name: str,
    description: str = "",
    enabled: bool = True,
    state: str = "ready",
    package_id: str = "org.test",
):
    descriptor = NS(
        id=plugin_id,
        display_name=name,
        description=description,
        package_id=package_id,
    )
    return NS(descriptor=descriptor, enabled=enabled, state=state)


def _method(name: str) -> str:
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginManagerPage")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_view_filter_labels_match_ux_contract():
    assert PLUGIN_VIEW_FILTERS == (
        ("all", "Tous"),
        ("active", "Actifs"),
        ("disabled", "Désactivés"),
        ("attention", "À examiner"),
    )


def test_search_matches_plugin_name_description_identifier_and_package():
    package = {"package_id": "org.cifereca.kobo", "name": "Kobo Suite", "publisher": "cifereca"}
    rows = [
        _status("provider.kobo", name="Catalogue Kobo", description="Livres Kobo", package_id="org.cifereca.kobo"),
        _status("provider.paris", name="Paris", description="Bibliothèque", package_id="org.cifereca.paris"),
    ]
    assert filter_package_plugins(package, rows, query="Kobo") == [rows[0], rows[1]]
    foreign_package = {"package_id": "org.other", "name": "Autre", "publisher": "tests"}
    assert filter_package_plugins(foreign_package, rows, query="Livres Kobo") == [rows[0]]
    assert filter_package_plugins(foreign_package, rows, query="provider.paris") == [rows[1]]


def test_state_filters_are_view_only_semantics():
    active = _status("a", name="A", enabled=True, state="ready")
    disabled = _status("b", name="B", enabled=False, state="disabled")
    blocked = _status("c", name="C", enabled=True, state="blocked")
    degraded = _status("d", name="D", enabled=True, state="degraded")
    error = _status("e", name="E", enabled=False, state="error")
    incompatible = _status("f", name="F", enabled=True, state="incompatible")
    package = {"package_id": "org.test", "name": "Test"}

    assert filter_package_plugins(package, [active, disabled, blocked], state_filter="active") == [active, blocked]
    assert filter_package_plugins(package, [active, disabled, blocked], state_filter="disabled") == [disabled]
    assert filter_package_plugins(
        package,
        [active, disabled, blocked, degraded, error, incompatible],
        state_filter="attention",
    ) == [blocked, degraded, error, incompatible]


def test_package_query_matches_name_identifier_and_publisher():
    package = {"package_id": "org.cifereca.kobo", "name": "Kobo Suite", "publisher": "Cifereca"}
    assert package_matches_query(package, "kobo")
    assert package_matches_query(package, "org.cifereca")
    assert package_matches_query(package, "cifereca")
    assert not package_matches_query(package, "paris")


def test_search_and_filter_callbacks_only_rerender_inventory():
    search = _method("_plugin_search_changed")
    state_filter = _method("_plugin_filter_changed")
    assert "self._render_inventory()" in search
    assert "self._render_inventory()" in state_filter
    combined = search + state_filter
    assert "set_enabled" not in combined
    assert "_toggle" not in combined
    assert "refresh()" not in combined


def test_refresh_keeps_search_controls_outside_results_container():
    source = _method("refresh")
    assert "self.plugin_search = Gtk.SearchEntry()" in source
    assert "self.plugin_state_filter = Gtk.DropDown.new_from_strings(filter_labels)" in source
    assert "self.inventory_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)" in source
    assert "self._render_inventory(statuses=statuses, inventory=inventory)" in source


def test_empty_state_is_contextualized():
    source = _method("_render_inventory")
    assert "Aucun plugin ne correspond à" in source
    assert "self._view_query" in source
    assert "self._view_filter" in source
