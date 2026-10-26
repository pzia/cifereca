"""P0 #1127: detached Settings widgets cannot mutate Diagnostics or navigate."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.settings_lifetime import SettingsPageLifetime


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_diagnostics_actions.py"


def _source_and_callback(name, namespace):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    build = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bind_settings_diagnostics")
    owner = build
    if name == "clear_diagnostics":
        owner = ast.parse(MAIN.with_name("settings_diagnostic_actions.py").read_text(encoding="utf-8"))
    func = next(node for node in ast.walk(owner)
                if isinstance(node, ast.FunctionDef) and node.name == name)
    scope = dict(namespace)
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[func], type_ignores=[]),
    ), str(MAIN), "exec"), scope)
    return ast.unparse(build), scope[name]


def _context(*, closed=False):
    events = []
    lifetime = SettingsPageLifetime()
    host = NS(
        _closed=closed,
        diagnostics=NS(clear=lambda: events.append(("legacy_clear",))),
        toast=lambda message: events.append(("toast", message)),
        navigate=lambda route: events.append(("navigate", route)),
        plugins_page=NS(
            focus_plugin=lambda source: events.append(("focus", source)),
        ),
    )
    return {
        "self": host,
        "page_lifetime": lifetime,
        "refresh_text": lambda: events.append(("refresh",)),
        "diag_text": NS(get_buffer=lambda: NS(
            set_text=lambda value: events.append(("display", value))
        )),
    }, lifetime, host, events


def test_diagnostics_clear_button_clears_only_display_when_live():
    ctx, lifetime, host, events = _context()
    source, clear = _source_and_callback("clear_diagnostics", ctx)
    assert 'clear.connect(\'clicked\', clear_diagnostics)' in source
    clear()
    assert events == [
        ("display", ""),
        ("toast", "Affichage effacé ; journal local conservé"),
    ]
    assert lifetime.alive


def test_diagnostics_clear_button_cannot_modify_core_after_rebuild():
    ctx, lifetime, host, events = _context()
    _, clear = _source_and_callback("clear_diagnostics", ctx)
    lifetime.dispose()
    clear()
    assert events == []


def test_diagnostics_clear_button_cannot_modify_core_after_window_close():
    ctx, lifetime, host, events = _context(closed=True)
    _, clear = _source_and_callback("clear_diagnostics", ctx)
    clear()
    assert events == []


def test_live_plugin_link_still_navigates_and_focuses():
    ctx, lifetime, host, events = _context()
    _, open_plugin = _source_and_callback("open_diagnostic_plugin", ctx)
    open_plugin(None, "provider-kobo")
    assert events == [
        ("navigate", "plugins"),
        ("focus", "provider-kobo"),
    ]


def test_old_plugin_link_neither_navigates_nor_focuses_after_rebuild_or_close():
    for closed, dispose in [(False, True), (True, False)]:
        ctx, lifetime, host, events = _context(closed=closed)
        _, open_plugin = _source_and_callback("open_diagnostic_plugin", ctx)
        if dispose:
            lifetime.dispose()
        open_plugin(None, "provider-kobo")
        assert events == []
