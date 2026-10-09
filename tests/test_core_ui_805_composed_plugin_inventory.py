from __future__ import annotations

import ast
from pathlib import Path

from cifereca.plugin_api import ContractRequirement, Dependency, PluginDescriptor, PluginStatus
from cifereca.gui.plugin_view import (
    PLUGIN_ROLE_FILTERS, PLUGIN_VIEW_FILTERS, filter_package_plugins,
    plugin_dependents, plugin_relationships, plugin_role,
)

PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def _row(
    identifier: str, category: str = "recommendation", *, package: str = "sample",
    enabled: bool = True, state: str = "ready",
    requires=(), recommends=(), contracts=(),
) -> PluginStatus:
    descriptor = PluginDescriptor(
        id=identifier, type=category, version="1.0", package_id=package,
        package_version="1.0", display_name=identifier.replace(".", " "),
        requires=tuple(Dependency(id_) for id_ in requires),
        recommends=tuple(Dependency(id_) for id_ in recommends),
        requires_contracts=tuple(
            ContractRequirement(category=item, contract="contract/1")
            for item in contracts
        ),
    )
    return PluginStatus(descriptor, state=state, enabled=enabled, effective_priority=0)


def _method(name: str) -> str:
    parsed = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in parsed.body if isinstance(node, ast.ClassDef)
               and node.name == "PluginManagerPage")
    return ast.unparse(next(node for node in cls.body
                            if isinstance(node, ast.FunctionDef) and node.name == name))


def _inventory():
    """36 deterministic items; several child extensions per main plugin."""
    rows = []
    for family in ("youth", "criticism", "constellations", "reading"):
        base = family + ".main"
        rows.append(_row(base, "ui.page", package=family))
        for index in range(1, 9):
            category = (
                "provider.library" if index % 4 == 0 else
                "book.detail.enrichment" if index % 4 == 1 else
                "runtime.adapter" if index % 4 == 2 else "recommendation"
            )
            rows.append(_row(
                family + ".extension." + str(index), category,
                package=family + "-extensions",
                requires=(base,),
            ))
    return rows


def test_role_filters_are_additive_and_state_filters_unchanged():
    assert PLUGIN_ROLE_FILTERS == (
        ("all", "Tous les rôles"), ("feature", "Fonctionnalités"),
        ("source", "Sources"), ("service", "Services"),
        ("ui", "Interface"), ("technical", "Techniques"),
    )
    assert tuple(key for key, _label in PLUGIN_VIEW_FILTERS) == (
        "all", "active", "disabled", "attention",
    )


def test_category_mapping_is_manifest_based_not_guesswork():
    mapping = {
        "provider.library": "source", "source.youth": "source",
        "storage.target": "source", "catalog.service": "service",
        "catalog.enrichment": "service", "book.detail.enrichment": "service",
        "ui.page": "ui", "ui.widgets": "ui",
        "transport": "technical", "drm.handler": "technical",
        "runtime.adapter": "technical", "device.profile": "technical",
        "maintenance.tool": "technical", "diagnostics.check": "technical",
        "recommendation": "feature", "document.transformer": "feature",
        "sync": "feature",
    }
    for category, role in mapping.items():
        assert plugin_role(_row("test", category).descriptor) == role


def test_role_filter_and_status_filter_are_composable_without_mutation():
    rows = [
        _row("ui", "ui.page"),
        _row("source", "provider.library", enabled=False, state="disabled"),
        _row("bad-source", "provider.library", state="blocked"),
        _row("broken-ui", "ui.page", state="incompatible"),
    ]
    package = {"package_id": "sample", "name": "Suite"}
    original = [(r.enabled, r.state) for r in rows]
    assert filter_package_plugins(package, rows, role_filter="source") == rows[1:3]
    assert filter_package_plugins(
        package, rows, role_filter="source", state_filter="attention",
    ) == [rows[2]]
    assert filter_package_plugins(
        package, rows, role_filter="ui", state_filter="attention",
    ) == [rows[3]]
    assert filter_package_plugins(
        package, rows, role_filter="source", state_filter="active",
    ) == [rows[2]]
    assert [(r.enabled, r.state) for r in rows] == original


def test_large_inventory_has_stable_30_plus_group_and_role_counts():
    rows = _inventory()
    assert len(rows) == 36
    assert len({r.descriptor.package_id for r in rows}) == 8
    by_role = {
        role: filter_package_plugins(
            {"package_id": "any"}, rows, role_filter=role,
        )
        for role, _label in PLUGIN_ROLE_FILTERS if role != "all"
    }
    assert sum(map(len, by_role.values())) == 36
    assert len(by_role["ui"]) == 4
    assert len(by_role["source"]) == 8
    assert len(by_role["service"]) == 8
    assert len(by_role["technical"]) == 8
    assert len(by_role["feature"]) == 8
    for family in ("youth", "criticism", "constellations", "reading"):
        assert len(plugin_dependents(family + ".main", rows)) == 8


def test_relationships_only_use_declared_requires_recommends_contracts():
    base = _row("youth.main", "ui.page")
    plug = _row(
        "youth.ages", "book.detail.enrichment",
        requires=("youth.main",), recommends=("research.optional",),
        contracts=("catalog.youth",),
    )
    optional = _row("research.optional", "catalog.service", recommends=("youth.main",))
    lines = plugin_relationships(plug, [base, plug, optional])
    assert "Requiert : youth main" in lines
    assert "Recommande : research.optional" in lines  # absent plugin stays by ID
    assert "Contrats utilisés : catalog.youth" in lines
    assert "Requis par : youth ages" in plugin_relationships(
        base, [base, plug, optional],
    )
    assert "Recommandé pour : research optional" in plugin_relationships(
        base, [base, plug, optional],
    )
    assert plugin_relationships(_row("standalone"), [base]) == ()


def test_transitive_dependents_handle_cycles_and_do_not_invent_relations():
    a = _row("a", requires=("c",))
    b = _row("b", requires=("a",))
    c = _row("c", requires=("b",))
    rows = [c, b, a]
    assert [r.descriptor.id for r in plugin_dependents("a", rows)] == ["b"]
    assert [r.descriptor.id for r in plugin_dependents(
        "a", rows, transitive=True,
    )] == ["b", "c"]


def test_enabling_a_parent_does_not_change_optional_dependents():
    a = _row("main", enabled=False)
    b = _row("required", requires=("main",), state="blocked")
    c = _row("optional", recommends=("main",))
    assert plugin_dependents("main", [a, b, c]) == (b,)
    assert plugin_relationships(a, [a, b, c]) == (
        "Requis par : required", "Recommandé pour : optional",
    )


def test_long_relation_lists_are_bounded_and_sorted_for_accessible_labels():
    root = _row("root")
    dependents = [_row("dependency." + str(i), requires=("root",)) for i in range(25)]
    line, = plugin_relationships(root, [root, *reversed(dependents)])
    assert line.startswith("Requis par :")
    assert "(+21)" in line
    assert len(line) < 160


def test_role_ui_is_accessible_and_filters_without_refreshing_registry():
    refresh = _method("refresh")
    role = _method("_plugin_role_changed")
    inventory = _method("_render_inventory")
    assert "self.plugin_role_filter = Gtk.DropDown.new_from_strings(" in refresh
    assert "self.plugin_role_filter.set_tooltip_text(" in refresh
    assert "self.plugin_role_filter.connect('notify::selected', self._plugin_role_changed)" in refresh
    assert "self._render_inventory()" in role
    assert "set_enabled" not in role and "manager.activate" not in role
    assert "role_filter=self._view_role" in inventory
    assert "self._view_role != 'all'" in inventory


def test_package_expander_remembers_state_and_exposes_attention_when_closed():
    source = _method("_append_package")
    assert "Gtk.Expander()" in source
    assert "needs_attention" in source
    assert "à examiner" in source
    assert "self._package_expansion.get(package_id" in source
    assert "notify::expanded" in source
    assert "details.set_child(children)" in source
    assert "self._plugin_row(status, package, statuses=statuses)" in source


def test_plugin_card_exposes_manifest_relations_and_parent_disable_impact():
    card = _method("_plugin_row")
    assert "plugin_relationships(status, statuses)" in card
    assert "plugin_dependents(d.id, statuses, transitive=True)" in card
    assert "Désactivation : affectera également" in card
    assert "PLUGIN_ROLE_LABELS[plugin_role(d)]" in card
    assert "Activation ·" in card and "État ·" in card
    assert "self._friendly_problem(status)" in card
