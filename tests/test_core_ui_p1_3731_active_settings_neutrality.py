"""#3731: active Settings wiring must never import Kobo compatibility policy."""
from __future__ import annotations

import ast
from pathlib import Path


GUI = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
ACTIVE = ("settings_page.py", "settings_sync_actions.py", "settings_commit_actions.py")


def test_live_settings_bindings_contain_no_kobo_compatibility_imports_or_fields():
    for name in ACTIVE:
        source = (GUI / name).read_text(encoding="utf-8")
        module = ast.parse(source)
        assert "kobo_settings" not in source
        assert "refresh_kobo_backup_status" not in source
        assert "kobo_backup_action_state" not in source
        for entry in ast.walk(module):
            if isinstance(entry, ast.ImportFrom):
                assert not (entry.module or "").startswith("settings_legacy_kobo")
            if isinstance(entry, ast.Import):
                assert all("settings_legacy_kobo" not in alias.name for alias in entry.names)


def test_navigation_is_closed_to_all_provider_consent_side_effects():
    module = ast.parse((GUI / "settings_sync_actions.py").read_text(encoding="utf-8"))
    binder = next(
        entry for entry in module.body
        if isinstance(entry, ast.FunctionDef) and entry.name == "bind_settings_sync_actions"
    )
    nav = next(
        entry for entry in binder.body
        if isinstance(entry, ast.FunctionDef) and entry.name == "open_sync_page"
    )
    body = ast.unparse(nav)
    assert "settings_state.dirty('sync')" in body
    assert "self.navigate('sync')" in body
    assert "page_lifetime.alive" in body
    for forbidden in ("kobo_backup", "experimental_actions", "acquire", "download",
                      "run_kobo_backup", "set_source_sync_consent", "service."):
        assert forbidden not in body


def test_legacy_kobo_compatibility_is_not_reexported_from_live_binder():
    from cifereca.gui import settings_sync_actions, settings_sync_section
    assert not hasattr(settings_sync_actions, "kobo_backup_action_state")
    assert hasattr(settings_sync_section, "kobo_backup_action_state")
