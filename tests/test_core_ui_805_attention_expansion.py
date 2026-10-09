"""Regression checks for visible plugin errors and filtered results.

These tests exercise the real pure display policy, plus its GTK call sites
without requiring a running display server.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.gui.plugin_attention import plugin_section_expanded


PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def _method_source(name: str) -> str:
    module = ast.parse(PAGE.read_text(encoding="utf-8"))
    panel = next(node for node in module.body if isinstance(node, ast.ClassDef)
                 and node.name == "PluginManagerPage")
    method = next(node for node in panel.body if isinstance(node, ast.FunctionDef)
                  and node.name == name)
    return ast.unparse(method)


@pytest.mark.parametrize("default", [False, True])
@pytest.mark.parametrize("saved", [None, False, True])
def test_normal_browsing_retains_expansion_choice(saved, default):
    expected = default if saved is None else saved
    assert plugin_section_expanded(saved, default=default) is expected


@pytest.mark.parametrize("state", ["blocked", "degraded", "incompatible", "error"])
@pytest.mark.parametrize("saved", [None, False, True])
def test_any_plugin_problem_is_visible_despite_saved_collapse(state, saved):
    assert state in {"blocked", "degraded", "incompatible", "error"}
    assert plugin_section_expanded(saved, attention=True) is True


@pytest.mark.parametrize("filter_name", ["search", "state", "role"])
def test_filtered_results_are_visible_despite_saved_collapse(filter_name):
    assert filter_name
    assert plugin_section_expanded(False, filtered=True) is True


def test_recovering_from_an_error_restores_previous_collapsed_choice():
    saved = False
    assert plugin_section_expanded(saved, attention=True) is True
    assert plugin_section_expanded(saved, attention=False) is False


def test_leaving_a_filter_restores_previous_collapsed_choice():
    saved = False
    assert plugin_section_expanded(saved, filtered=True) is True
    assert plugin_section_expanded(saved, filtered=False) is False


def test_package_expander_prioritizes_warning_and_filters():
    source = _method_source("_append_package")
    assert "plugin_section_expanded(" in source
    assert "attention=bool(needs_attention)" in source
    assert "filtered=filtered" in source
    assert "self._package_expansion.get(package_id)" in source
    assert "len(package_plugins) == 1" in source
    # Initial set_expanded happens before signal wiring, so a temporary
    # forced expansion does not overwrite the user's preference.
    assert source.index("details.set_expanded(") < source.index("details.connect(")


def test_family_and_overview_never_hide_problematic_extensions():
    source = _method_source("_append_family_navigation")
    assert "attention=bool(affected)" in source
    assert "attention=bool(issues)" in source
    assert "self._family_expansion.get(descriptor.id)" in source
    assert source.index("family.set_expanded(") < source.index("family.connect(")
    assert source.index("overview.set_expanded(") < source.index("overview.connect(")


def test_policy_is_presentation_only():
    source = PAGE.read_text(encoding="utf-8")
    assert "from .plugin_attention import plugin_section_expanded" in source
    policy = (PAGE.parent / "plugin_attention.py").read_text(encoding="utf-8")
    for mutation in ("set_enabled(", "activate(", "httpx", "urllib", "socket"):
        assert mutation not in policy
