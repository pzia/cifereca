from __future__ import annotations

import ast
from pathlib import Path

from cifereca.plugin_api import Dependency, PluginDescriptor, PluginStatus
from cifereca.gui.plugin_view import functional_plugin_groups

PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def _row(
    identifier: str, *, package: str = "root", requires=(),
    state: str = "ready", enabled: bool = True,
) -> PluginStatus:
    d = PluginDescriptor(
        id=identifier, type="ui.page", version="1.0",
        package_id=package, package_version="1.0",
        display_name=identifier,
        requires=tuple(Dependency(name) for name in requires),
    )
    return PluginStatus(d, state=state, enabled=enabled, effective_priority=0)


def _groups(rows):
    return {
        root.descriptor.id: [child.descriptor.id for child in members]
        for root, members in functional_plugin_groups(rows)
    }


def _method(name: str) -> str:
    parsed = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(n for n in parsed.body if isinstance(n, ast.ClassDef)
               and n.name == "PluginManagerPage")
    return ast.unparse(next(n for n in cls.body
                            if isinstance(n, ast.FunctionDef) and n.name == name))


def test_36_plugins_in_different_packages_form_four_functional_families():
    rows = []
    for parent in ("constellations", "criticism", "reading", "youth"):
        rows.append(_row(parent, package=parent + "-main"))
        rows += [
            _row(parent + "." + str(index), package=parent + "-addon-" + str(index),
                 requires=(parent,))
            for index in range(8)
        ]
    families = _groups(reversed(rows))
    assert list(families) == sorted(families)
    assert len(families) == 4
    assert sum(len(children) for children in families.values()) == 32
    assert set(families) == {"constellations", "criticism", "reading", "youth"}
    assert len({child for children in families.values() for child in children}) == 32


def test_transitive_extension_attaches_to_primary_not_to_intermediate_package():
    groups = _groups([
        _row("youth", package="main"),
        _row("youth.catalog", package="providers", requires=("youth",)),
        _row("youth.booktrust", package="enrichers", requires=("youth.catalog",)),
    ])
    assert groups == {"youth": ["youth.booktrust", "youth.catalog"]}


def test_multiple_required_parents_choose_stable_root_without_duplicates():
    rows = [
        _row("z.main"), _row("a.main"),
        _row("child", requires=("z.main", "a.main")),
    ]
    for permutation in (rows, list(reversed(rows))):
        assert _groups(permutation) == {"a.main": ["child"]}


def test_missing_dependencies_cycles_and_names_do_not_invent_parent():
    rows = [
        _row("isolated.main"), _row("isolated.extension"),
        _row("orphan", requires=("uninstalled.plugin",)),
        _row("cycle.a", requires=("cycle.b",)),
        _row("cycle.b", requires=("cycle.a",)),
        _row("self", requires=("self",)),
    ]
    assert functional_plugin_groups(rows) == ()


def test_disabled_and_degraded_extensions_remain_in_family_index():
    groups = functional_plugin_groups([
        _row("root", state="disabled", enabled=False),
        _row("broken", requires=("root",), state="blocked"),
        _row("disabled", requires=("root",), state="disabled", enabled=False),
    ])
    assert len(groups) == 1
    root, children = groups[0]
    assert root.state == "disabled"
    assert {child.state for child in children} == {"blocked", "disabled"}


def test_group_index_is_separate_from_package_cards_and_filters():
    inventory = _method("_render_inventory")
    assert "self._append_family_navigation(statuses)" in inventory
    assert "not self._view_query" in inventory
    assert "self._view_filter == 'all'" in inventory
    assert "self._view_role == 'all'" in inventory
    assert "self._append_package(package, package_plugins, statuses=statuses)" in inventory


def test_accessible_navigation_and_collapse_are_stable_across_refresh():
    init = _method("__init__")
    family = _method("_append_family_navigation")
    focus = _method("focus_plugin")
    assert "self._family_navigation_open = True" in init
    assert "self._family_expansion" in init
    assert "Gtk.Expander(" in family
    assert "notify::expanded" in family
    assert "self._family_expansion.get(" in family
    assert "self._family_navigation_open" in family
    assert "Gtk.Button(" in family
    assert "set_tooltip_text" in family
    assert "self.focus_plugin(pid)" in family
    assert "self._view_filter = 'all'" in focus
    assert "self._view_role = 'all'" in focus


def test_family_overview_is_manifest_only_and_cannot_change_activation():
    family = _method("_append_family_navigation")
    assert "functional_plugin_groups(statuses)" in family
    assert "plugin_role(d)" in family
    assert "set_enabled" not in family
    assert "manager.activate" not in family
    assert "manager.disable" not in family
    assert "self._toggle(" not in family
