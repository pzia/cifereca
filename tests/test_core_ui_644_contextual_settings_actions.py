from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.gui.settings_context import settings_action_state


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src" / "cifereca" / "gui" / "main_window.py"


@pytest.mark.parametrize("tab", ["accounts", "library", "sync", "interface"])
def test_persistable_tabs_show_save_but_require_pending_changes(tab):
    clean = settings_action_state(tab, dirty=False)
    dirty = settings_action_state(tab, dirty=True)

    assert clean.show_save is True
    assert clean.save_sensitive is False
    assert dirty.show_save is True
    assert dirty.save_sensitive is True


@pytest.mark.parametrize("tab", ["accounts", "sync"])
def test_reconnect_is_limited_to_source_and_sync_contexts(tab):
    state = settings_action_state(tab, dirty=False)
    assert state.show_reconnect is True
    assert state.reconnect_sensitive is True


@pytest.mark.parametrize("tab", ["library", "interface", "diagnostic"])
def test_reconnect_is_hidden_outside_source_and_sync_contexts(tab):
    assert settings_action_state(tab, dirty=True).show_reconnect is False


def test_diagnostic_has_no_global_save_actions():
    state = settings_action_state("diagnostic", dirty=True)
    assert state.show_save is False
    assert state.show_reconnect is False


def test_settings_page_tracks_tab_changes_and_dirty_revisions():
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_build_settings_page")
    rendered = ast.unparse(method)

    assert 'tabs.connect("notify::visible-child-name", update_settings_actions)' in source
    assert "track_all_settings_widgets()" in rendered
    assert "settings_revisions[tab] = settings_revisions.get(tab, 0) + 1" in rendered
    assert "staged_settings_revisions = dict(settings_revisions)" in rendered
    assert "saved_settings_revisions[tab] = revision" in rendered
    assert rendered.index("def ok(result):") < rendered.index("saved_settings_revisions[tab] = revision")


def test_add_delete_account_and_identity_operations_mark_accounts_dirty():
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    assert source.count('mark_settings_dirty("accounts")') >= 4
