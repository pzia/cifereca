"""P1 #2139: Settings links to Diagnostics when present, without a dead route.

The plugin may be disabled, absent or rebuilt while Settings stays visible.
GTK and provider transports are deliberately not imported.
"""
from types import SimpleNamespace as NS

from cifereca.gui.settings_diagnostics_actions import bind_diagnostics_page_link
from cifereca.gui.settings_lifetime import SettingsPageLifetime


class Button:
    def __init__(self):
        self.enabled = True
        self.tooltip = None
        self.clicked = None

    def set_sensitive(self, enabled):
        self.enabled = enabled

    def set_tooltip_text(self, text):
        self.tooltip = text

    def connect(self, signal, callback):
        assert signal == "clicked" and self.clicked is None
        self.clicked = callback

    def click(self):
        self.clicked(self)


def test_route_present_navigates_only_after_explicit_click():
    calls = []
    window = NS(_closed=False, _pages={"diagnostics": object()},
                navigate=lambda route: calls.append(route))
    button = Button()
    refresh = bind_diagnostics_page_link(
        window, button, page_lifetime=SettingsPageLifetime(),
    )
    assert button.enabled
    assert "journaux" in button.tooltip
    assert calls == []
    refresh()
    assert calls == []
    button.click()
    assert calls == ["diagnostics"]


def test_absent_or_disabled_plugin_stays_on_core_fallback():
    calls = []
    window = NS(_closed=False, _pages={},
                navigate=lambda route: calls.append(route))
    button = Button()
    refresh = bind_diagnostics_page_link(
        window, button, page_lifetime=SettingsPageLifetime(),
    )
    assert button.enabled is False
    assert "outils locaux" in button.tooltip
    button.click()  # Even programmatic stale clicks cannot enter a dead route.
    assert calls == []
    window._pages["diagnostics"] = object()
    refresh()
    assert button.enabled
    button.click()
    assert calls == ["diagnostics"]
    window._pages.clear()
    button.click()  # Revalidated at click time, without route navigation.
    assert calls == ["diagnostics"]
    assert button.enabled is False


def test_rebuilt_or_closed_settings_cannot_navigate():
    calls = []
    window = NS(_closed=False, _pages={"diagnostics": object()},
                navigate=lambda route: calls.append(route))
    stale = SettingsPageLifetime()
    button = Button()
    refresh = bind_diagnostics_page_link(window, button, page_lifetime=stale)
    stale.dispose()
    button.click()
    refresh()
    assert calls == []
    current = SettingsPageLifetime()
    new_button = Button()
    bind_diagnostics_page_link(window, new_button, page_lifetime=current)
    window._closed = True
    new_button.click()
    assert calls == []


def test_no_plugin_manager_or_capture_required_to_use_entrypoint():
    calls = []
    window = NS(_closed=False, _pages={"diagnostics": object()},
                navigate=lambda route: calls.append(route))
    button = Button()
    bind_diagnostics_page_link(
        window, button, page_lifetime=SettingsPageLifetime(),
    )
    button.click()
    assert calls == ["diagnostics"]
    assert not hasattr(window, "plugin_manager")
    assert not hasattr(window, "ui_perf")


def test_route_visibility_moves_tools_without_mutating_capture_or_service():
    class Group:
        def __init__(self): self.visible = None
        def set_visible(self, value): self.visible = value
    groups = [Group(), Group(), Group()]
    window = NS(_closed=False, _pages={}, navigate=lambda route: None)
    lifetime = SettingsPageLifetime()
    button = Button()
    refresh = bind_diagnostics_page_link(window, button, page_lifetime=lifetime,
                                         fallback_widgets=groups)
    assert all(g.visible is True for g in groups)
    window._pages["diagnostics"] = object()
    assert refresh() is True
    assert all(g.visible is False for g in groups)
    assert button.enabled is True
    window._pages.clear()
    assert refresh() is False
    assert all(g.visible is True for g in groups)
    lifetime.dispose()
    window._pages["diagnostics"] = object()
    refresh()
    assert all(g.visible is True for g in groups)
    assert not hasattr(window, "ui_perf")
    assert not hasattr(window, "service")
