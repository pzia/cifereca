"""P0 #1127: stale asynchronous Settings UI callbacks are inert."""
from __future__ import annotations
from tests.settings_source import settings_source_text

import ast
from pathlib import Path

from cifereca.gui.settings_lifetime import SettingsPageLifetime


def test_new_sessions_accept_current_token_only():
    first, second = SettingsPageLifetime(), SettingsPageLifetime()
    a = first.new_token()
    b = first.new_token()
    c = second.new_token()
    assert not first.accepts(a)
    assert first.accepts(b) and second.accepts(c)


def test_disposal_is_idempotent_and_disallows_any_future_result():
    session = SettingsPageLifetime()
    before = session.new_token()
    session.dispose()
    session.dispose()
    assert not session.alive
    assert not session.accepts(before)
    assert not session.accepts(session.new_token())


def test_token_type_does_not_coerce_boolean_into_authorization():
    session = SettingsPageLifetime()
    assert not session.accepts(True)
    assert session.accepts(session.new_token())
    assert not session.accepts(1.0)


def test_dispose_is_presentation_only_and_does_not_touch_external_services():
    session = SettingsPageLifetime()
    assert session.alive
    session.dispose()
    assert not session.alive


def test_window_lifecycle_disposes_on_close_rebuild_and_standalone():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    tree = ast.parse(path.read_text("utf-8"))
    window = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                  and n.name == "MainWindow")
    get = lambda name: ast.unparse(next(
        n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == name
    ))
    init = get("__init__")
    build = ast.unparse(ast.parse(settings_source_text()))
    close = get("_on_close_request")
    facade = get("_open_prelogin_diagnostics")
    assert "standalone_settings_controller.open_standalone_settings(" in facade
    standalone = (path.parent / "standalone_settings_controller.py").read_text(encoding="utf-8")
    assert "SettingsPageLifetime()" in build
    assert "previous.dispose()" in build
    assert "page_lifetime.alive" in build
    assert "'_settings_main_lifetime'" in build
    assert "'_settings_standalone_lifetime'" in build
    assert "getattr(self, lifetime_name, None)" in build
    assert "close_window_resources(self, source_remove=GLib.source_remove)" in close
    teardown = (path.parent / "window_close.py").read_text(encoding="utf-8")
    assert "lifetime.dispose()" in teardown
    assert "standalone_lifetime.dispose()" in standalone
    assert "window._diag_refresh = None" in teardown
    assert "_settings_main_lifetime" in init


def test_stale_ai_callbacks_are_gated_but_explicit_approval_remains():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    text = (path.parent / "settings_ai_actions.py").read_text("utf-8")
    assert "version == ai_probe_version[0] and page_lifetime.alive" in text
    assert "if not page_lifetime.alive:" in text
    assert "confirmation_version != ai_probe_version[0]" in text
    assert "submit_ai_probe(settings, confirmed=True)" in text
    assert "probe_llm_connection_result(" in text
