from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.plugin_view import (
    filter_package_plugins,
    plugin_extension_packages,
)


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/cifereca/gui/plugin_manager_page.py"


def _status(
    plugin_id: str, package: str, category: str, *,
    requires_contracts=(), state="ready", enabled=True,
):
    return NS(
        descriptor=NS(
            id=plugin_id,
            package_id=package,
            type=category,
            display_name=plugin_id,
            description="",
            requires_contracts=tuple(
                NS(category=c) for c in requires_contracts
            ),
        ),
        state=state,
        enabled=enabled,
    )


def _method(name: str) -> str:
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginManagerPage")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_youth_source_packages_group_beneath_actual_contract_consumer():
    statuses = [
        _status("catalog.youth", "youth-core", "catalog.youth"),
        _status(
            "ui.page.youth", "youth-core", "ui.page",
            requires_contracts=("youth.source", "youth.bibliography"),
        ),
        _status("youth.source.booktrust", "booktrust", "youth.source"),
        _status("youth.source.milan", "milan", "youth.source"),
        _status("youth.detail", "youth-detail", "book.detail.enrichment"),
        _status("page.constellations", "constellations", "ui.page"),
    ]
    result = plugin_extension_packages(statuses)
    assert result == {
        "booktrust": ("youth-core", ("youth.source",)),
        "milan": ("youth-core", ("youth.source",)),
    }


def test_ambiguous_consumer_does_not_invent_an_arbitrary_parent():
    statuses = [
        _status("ui.a", "feature-a", "ui.page", requires_contracts=("source.external",)),
        _status("ui.b", "feature-b", "ui.page", requires_contracts=("source.external",)),
        _status("source.example", "source", "source.external"),
    ]
    assert plugin_extension_packages(statuses) == {}


def test_plugin_names_and_common_prefix_do_not_establish_parentage():
    statuses = [
        _status("ui.page.youth", "youth-core", "ui.page"),
        _status("youth.source.example", "source", "youth.source"),
    ]
    assert plugin_extension_packages(statuses) == {}


def test_feature_or_page_package_is_not_hidden_as_an_extension():
    statuses = [
        _status("ui.page.review", "review", "ui.page", requires_contracts=("criticism.source",)),
        _status("source.rss", "mixed", "criticism.source"),
        _status("ui.page.extra", "mixed", "ui.page"),
    ]
    assert plugin_extension_packages(statuses) == {}


def test_multiple_extensions_over_30_plugins_are_deterministically_grouped():
    statuses = [
        _status("ui.main", "primary", "ui.page", requires_contracts=("catalog.extra",)),
        *[
            _status(f"source.{index}", f"source-package-{index:02d}", "catalog.extra")
            for index in range(36)
        ],
        _status("independent", "standalone", "ui.page"),
    ]
    forward = plugin_extension_packages(statuses)
    reversed_input = plugin_extension_packages(list(reversed(statuses)))
    assert forward == reversed_input
    assert len(forward) == 36
    assert set(forward.values()) == {("primary", ("catalog.extra",))}


def test_status_filter_does_not_hide_blocked_child_or_invent_state():
    statuses = [
        _status("ui.main", "feature", "ui.page", requires_contracts=("source.external",)),
        _status("source.one", "extension", "source.external", state="blocked"),
    ]
    assert plugin_extension_packages(statuses) == {
        "extension": ("feature", ("source.external",)),
    }
    child = statuses[1]
    assert filter_package_plugins(
        {"package_id": "extension", "name": "Extension"},
        [child], state_filter="attention",
    ) == [child]


def test_gtk_inventory_preserves_standalone_filtered_extensions_and_attention():
    source = _method("_render_inventory")
    assert "plugin_extension_packages(statuses)" in source
    assert "if child_id in visible and parent_id in visible" in source
    assert "if package_id not in visible or package_id in child_ids" in source
    assert "attached.set_expanded(" in source
    assert "or self._view_role != 'all' or attention" in source
    assert "' · {attention} à examiner'" in source
    assert "Gtk.Expander()" in source
    assert "Étend via : " in source
    assert "self._append_package(package, package_plugins, statuses=statuses)" in source
    assert "target=attached_box" in source


def test_extension_disclosure_and_package_actions_remain_separate():
    source = _method("_append_package")
    assert "target = target if target is not None" in source
    assert "self._confirm_remove" in source
    assert "details.set_child(children)" in source
    assert "Gtk.Expander()" in source
