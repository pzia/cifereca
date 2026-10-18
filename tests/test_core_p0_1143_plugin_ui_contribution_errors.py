"""P0 #1143: plugin UI factory failures must never enter exported Diagnostics.

Run the actual headless controllers and existing simulated GTK harnesses;
no plugin activation, network, real GTK or user data are required.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.plugin_reload_controller import plugins_changed
from tests.test_core_ui_1129_query_widget_factory_controller import _setup
from tests.test_core_ui_1130_shell_header_controller import _window as header_window, _build
from tests.test_core_ui_1130_shell_page_controller import (
    _host as page_host, _page, _rebuild,
)
from tests.test_core_ui_1130_plugin_reload_controller import _window as reload_window


PRIVATE = "https://private.invalid/?auth=PRIVATE_PLUGIN_UI"


class HostilePluginError(RuntimeError):
    def __str__(self):
        raise AssertionError("Plugin exception text is not safe")

    def __repr__(self):
        raise AssertionError("Plugin exception repr is not safe")


def _fail(*_args, **_kwargs):
    raise HostilePluginError(PRIVATE)


def _assert_safe(events):
    text = str(events)
    assert "PRIVATE_PLUGIN_UI" not in text
    assert "private.invalid" not in text
    assert "HostilePluginError" not in text


def test_query_widget_factory_failure_falls_back_without_raw_diagnostic():
    catalog = NS(build_query_controls=_fail)
    window, events, fallback_calls, changed, activate, caps, create = _setup(
        catalog=catalog,
    )
    result = create()
    assert result.changed is changed and result.activate is activate
    assert len(fallback_calls) == 1
    assert ("diagnostic", ("PLUGIN", "query widget", "error"),
            {"detail": {"code": "query_widget_factory_failed"}}) in events
    _assert_safe(events)


def test_titlebar_factory_failure_preserves_adw_fallback_with_safe_trace():
    manager = NS(
        select=lambda _: NS(descriptor=NS(id="plugin.private")),
        service=lambda _: NS(build=_fail),
    )
    window, events = header_window(manager)
    result = _build(window)
    assert result is not None
    assert window._titlebar_service is None
    rows = [event for event in events if event[0] == "diagnostic"]
    assert rows == [
        ("diagnostic", ("PLUGIN", "titlebar build", "error"),
         {"detail": {"code": "titlebar_factory_failed"}}),
    ]
    _assert_safe(rows)


def test_page_factory_error_keeps_settings_recovery_without_private_route(
    monkeypatch,
):
    host, removed, names, navigation, events = page_host(
        monkeypatch,
        contributions=[_page("settings", priority=50, factory=_fail)],
        visible="settings",
    )
    recorded = []
    host.diagnostics = NS(
        add=lambda *args, **kwargs: recorded.append((args, kwargs))
    )
    _rebuild(host)
    assert navigation == ["settings"]
    assert set(host._pages) == {"settings", "plugins"}
    assert host._pages["settings"].priority == -10000
    assert recorded == [
        (("PLUGIN", "ui.page build", "error"),
         {"detail": {"code": "page_factory_failed"}}),
    ]
    _assert_safe(recorded)


def test_plugin_reload_failure_recovers_header_and_pages_with_safe_trace():
    window, calls, initial, updated = reload_window()
    window.service.attach_plugin_manager = _fail
    assert plugins_changed(window) is None
    assert window.shell_header is updated
    assert calls[0] == (
        "diagnostic",
        ("PLUGIN", "provider refresh after plugin change", "error"),
        {"detail": {"code": "provider_refresh_failed"}, "level": "detailed"},
    )
    assert [row[0] for row in calls[1:]] == [
        "remove", "header", "prepend", "menu", "pages", "accounts", "subscribe",
    ]
    _assert_safe(calls)
