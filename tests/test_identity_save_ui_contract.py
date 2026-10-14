from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import tempfile

import pytest

from cifereca.core import CiferecaCore


def _settings_save_nodes():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_commit_actions.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    build = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bind_settings_commit")
    save = next(node for node in ast.walk(build) if isinstance(node, ast.FunctionDef) and node.name == "save_settings")
    ok = next(node for node in save.body if isinstance(node, ast.FunctionDef) and node.name == "ok")
    return save, ok


def _self_calls(node):
    calls = []
    for item in ast.walk(node):
        if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Attribute):
            continue
        target = item.func.value
        if isinstance(target, ast.Name) and target.id == "self":
            calls.append(item.func.attr)
    return calls


def test_successful_identity_save_refreshes_selectors_delegations_and_home():
    save, ok = _settings_save_nodes()
    calls = _self_calls(ok)
    # _populate_accounts refreshes both the identity selector and delegated-account selector.
    assert "_populate_accounts" in calls
    # Dashboard is the current Home projection.
    assert "_render_dashboard" in calls

    # Refreshes belong to the success callback only: the save path must not publish
    # a new selection before service.save_configuration() has completed.
    direct_save_calls = []
    for stmt in save.body:
        if isinstance(stmt, ast.FunctionDef):
            continue
        direct_save_calls.extend(_self_calls(stmt))
    assert "_populate_accounts" not in direct_save_calls
    assert "_render_dashboard" not in direct_save_calls


def test_successful_save_preserves_active_identity():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        core = CiferecaCore.open(config_path=root / "config.json", database_path=root / "library.sqlite3")
        try:
            data = deepcopy(core.config.data)
            data["identities"] = [
                {"id": "alice", "label": "Alice", "directory_name": "alice", "preferences": {"reading_languages": ["fr"]}},
                {"id": "bob", "label": "Bob", "directory_name": "bob", "preferences": {"reading_languages": ["fr"]}},
            ]
            core.library.save_configuration(data)
            core.library.select_identity("bob")

            updated = deepcopy(core.config.data)
            updated["identities"][0]["label"] = "Alice modifiée"
            core.library.save_configuration(updated)

            assert core.library.current_identity is not None
            assert core.library.current_identity.identity_id == "bob"
        finally:
            core.close()


def test_failed_save_does_not_change_active_identity():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        core = CiferecaCore.open(config_path=root / "config.json", database_path=root / "library.sqlite3")
        try:
            data = deepcopy(core.config.data)
            data["identities"] = [
                {"id": "alice", "label": "Alice", "directory_name": "alice", "preferences": {"reading_languages": ["fr"]}},
                {"id": "bob", "label": "Bob", "directory_name": "bob", "preferences": {"reading_languages": ["fr"]}},
            ]
            core.library.save_configuration(data)
            core.library.select_identity("bob")

            invalid = deepcopy(core.config.data)
            invalid["identities"][0]["directory_name"] = "shared"
            invalid["identities"][1]["directory_name"] = "shared"
            with pytest.raises(Exception):
                core.library.save_configuration(invalid)

            assert core.library.current_identity is not None
            assert core.library.current_identity.identity_id == "bob"
        finally:
            core.close()
