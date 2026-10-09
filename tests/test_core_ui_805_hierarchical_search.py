"""Manifest-only cross-package search, with 30+ installed plugins."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.gui.plugin_view import (
    filter_package_plugins, required_extension_ids,
)
from cifereca.plugin_api import Dependency, PluginDescriptor, PluginStatus


PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def row(identifier, *, package, name=None, requires=(), recommends=(),
        state="ready", enabled=True, category="ui.page"):
    d = PluginDescriptor(
        id=identifier,
        type=category,
        version="1.0",
        package_id=package,
        package_version="1.0",
        display_name=name or identifier,
        requires=tuple(Dependency(p) for p in requires),
        recommends=tuple(Dependency(p) for p in recommends),
    )
    return PluginStatus(d, state=state, enabled=enabled, effective_priority=0)


def inventory():
    rows = []
    for group, name in enumerate(("Jeunesse", "Critique", "Constellations", "Recommandations")):
        root = name.lower()
        rows.append(row(root, package=root, name=name))
        for i in range(8):
            rows.append(row(
                f"addon.{group}.{i}",
                package=f"pkg-addon-{group}-{i}",
                name=f"Extension {i}",
                requires=(root,),
                category="book.detail.enrichment" if i % 2 else "catalog.service",
                state="blocked" if i == 7 else "ready",
            ))
    assert len(rows) == 36
    return rows


def test_searching_a_principal_finds_all_32_required_extensions_across_packages():
    rows = inventory()
    for group, root in enumerate(("jeunesse", "critique", "constellations", "recommandations")):
        ids = required_extension_ids(reversed(rows), root)
        assert len(ids) == 8
        assert ids == {f"addon.{group}.{i}" for i in range(8)}
        assert root not in ids
        # The extension package name is deliberately unrelated to the root's
        # display name; cross-package relation is proven by the manifest.
        extension = next(r for r in rows if r.descriptor.id == f"addon.{group}.0")
        package = {"package_id": extension.descriptor.package_id,
                   "name": "Extension indépendante"}
        assert filter_package_plugins(package, [extension], query=root, related_ids=ids) == [extension]
        assert filter_package_plugins(package, [extension], query=root) == []


def test_nested_extensions_are_found_without_confusing_optional_recommendations():
    rows = [
        row("Youth.main", name="Jeunesse", package="app"),
        row("source", package="source-pkg", requires=("Youth.main",)),
        row("feed", package="feed-pkg", requires=("source",)),
        row("optional", package="optional-pkg", recommends=("Youth.main",)),
        row("sibling", package="unrelated", requires=("missing-parent",)),
    ]
    assert required_extension_ids(rows, "Jeunesse") == {"source", "feed"}
    assert required_extension_ids(rows, "Youth.main") == {"source", "feed"}
    assert required_extension_ids(rows, "missing-parent") == frozenset()
    assert required_extension_ids(rows, "") == frozenset()


def test_search_is_case_insensitive_and_uses_display_name_not_package_guessing():
    rows = [
        row("main", package="main-pkg", name="Critique littéraire"),
        row("child", package="rss-pkg", requires=("main",)),
        row("other", package="rss-pkg", recommends=("main",)),
    ]
    assert required_extension_ids(rows, "CRITIQUE LITT") == {"child"}
    assert required_extension_ids(rows, "rss-pkg") == frozenset()


def test_cycles_missing_parents_and_duplicates_never_create_false_descendants():
    rows = [
        row("root", package="p"),
        row("a", package="p", requires=("root", "b")),
        row("b", package="p", requires=("a",)),
        row("uninstalled", package="p", requires=("missing",)),
        row("self", package="p", requires=("self",)),
    ]
    assert required_extension_ids(rows, "root") == {"a", "b"}
    assert required_extension_ids(rows, "missing") == frozenset()
    assert required_extension_ids(rows, "self") == frozenset()


def test_role_and_attention_filters_still_take_precedence_over_family_search():
    rows = inventory()
    ids = required_extension_ids(rows, "Jeunesse")
    extensions = [r for r in rows if r.descriptor.id in ids]
    package = {"package_id": "unrelated", "name": "Other"}
    assert len(filter_package_plugins(
        package, extensions, query="jeunesse", related_ids=ids,
        state_filter="attention",
    )) == 1
    assert filter_package_plugins(
        package, extensions, query="jeunesse", related_ids=ids,
        role_filter="source",
    ) == []
    assert len(filter_package_plugins(
        package, extensions, query="jeunesse", related_ids=ids,
        role_filter="service",
    )) == 8
    assert len(filter_package_plugins(
        package, extensions, query="jeunesse", related_ids=ids,
        state_filter="disabled",
    )) == 0


def test_manager_computes_related_ids_once_and_never_activates_plugins():
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "PluginManagerPage")
    body = ast.unparse(next(node for node in cls.body
                            if isinstance(node, ast.FunctionDef)
                            and node.name == "_render_inventory"))
    assert "related_ids = required_extension_ids(statuses, self._view_query)" in body
    assert "related_ids=related_ids" in body
    assert body.count("related_ids = required_extension_ids(") == 1
    assert "manager.activate" not in body and "set_enabled" not in body
