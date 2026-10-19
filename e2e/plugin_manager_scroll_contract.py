"""Real Gtk4/Xvfb contract for plugin viewport and focus (#908).

Called from the existing book-detail Core E2E fixtures at 1366/1600.
No provider network access, ZIP install, personal config or extra workflow.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from gi.repository import Gtk

from cifereca.gui.plugin_manager_page import PluginManagerPage


class FixtureManager:
    """Thirty-six installed contributions, including cross-package extensions."""
    def __init__(self):
        self.rows = []
        self.calls = []
        for index in range(36):
            identifier = f"fixture.plugin.{index:02d}"
            descriptor = NS(
                id=identifier, package_id=f"fixture.pkg.{index:02d}",
                display_name=f"Fixture module {index:02d}",
                description="Local-only GTK scroll fixture",
                help_text="", config_schema={}, type="ui.page",
                instantiation="multiple",
                requires=tuple(
                    [NS(plugin="fixture.plugin.16")] if index in (17, 18) else ()
                ),
                recommends=(), requires_contracts=(),
            )
            self.rows.append(NS(
                descriptor=descriptor, enabled=True, state="ready",
                reason=None, effective_priority=0,
            ))

    def statuses(self):
        return tuple(self.rows)

    def package_inventory(self, *, include_expected=False):
        return [
            {
                "package_id": row.descriptor.package_id,
                "name": row.descriptor.display_name,
                "active": "1.0.0", "builtin": True,
                "freshness": "up_to_date",
            }
            for row in self.rows
        ]

    def set_enabled(self, identifier, enabled):
        self.calls.append((identifier, enabled))
        row = next(row for row in self.rows if row.descriptor.id == identifier)
        row.enabled = enabled
        row.state = "ready" if enabled else "disabled"


def verify_plugin_scroll(window, later, done, *, width: int):
    """Exercise a real mapped GtkScrolledWindow across two toggle rebuilds."""
    assert width in (1024, 1366)
    manager = FixtureManager()
    changed = []
    page = PluginManagerPage(window, manager, on_changed=lambda: changed.append(True))
    test_window = Gtk.Window()
    test_window.set_application(window.get_application())
    test_window.set_transient_for(window)
    test_window.set_default_size(width, 650)
    test_window.set_child(page)
    test_window.present()

    target = "fixture.plugin.15"
    state = {"phase": 0}

    def geometry():
        widget = page._plugin_widgets[target]
        success, bounds = widget.compute_bounds(page.outer)
        assert success and widget.get_mapped(), "Fixture plugin row has no GTK allocation"
        return bounds.get_y(), page.get_vadjustment().get_value()

    def initial_scroll():
        adjustment = page.get_vadjustment()
        assert len(page._plugin_widgets) == 36
        assert adjustment.get_upper() - adjustment.get_page_size() > 500, (
            "Synthetic GTK inventory does not overflow"
        )
        row_y, _ = geometry()
        adjustment.set_value(min(
            row_y - 170,
            adjustment.get_upper() - adjustment.get_page_size(),
        ))
        later(180, first_toggle)

    def first_toggle():
        switch = page._plugin_switches[target]
        assert switch.grab_focus() is not False
        row_y, scroll = geometry()
        assert scroll > 250, "GTK plugin list did not scroll to middle"
        state["offset"] = row_y - scroll
        switch.set_active(False)
        assert manager.calls == [(target, False)]
        later(200, verify_first_toggle)

    def verify_first_toggle():
        row_y, scroll = geometry()
        assert scroll > 250, "Viewport reset to top after deactivation"
        assert abs(row_y - scroll - state["offset"]) <= 60, (
            "GTK plugin row lost its visual anchor"
        )
        switch = page._plugin_switches[target]
        assert not switch.get_active(), "Plugin state did not reflect deactivation"
        assert switch.has_focus(), "GTK focus lost by switch reconstruction"
        state["offset"] = row_y - scroll
        switch.set_active(True)
        assert manager.calls[-1] == (target, True)
        later(200, verify_second_toggle)

    def verify_second_toggle():
        row_y, scroll = geometry()
        assert scroll > 250, "Viewport reset to top after activation"
        assert abs(row_y - scroll - state["offset"]) <= 60, (
            "GTK plugin row shifted after activation"
        )
        assert page._plugin_switches[target].has_focus()
        assert manager.calls == [(target, False), (target, True)]
        assert len(changed) == 2
        # Real package expansion and family hierarchy must survive both writes.
        assert len(page._plugin_widgets) == 36
        assert page._family_navigation_open is True
        test_window.destroy()
        done()

    later(420, initial_scroll)


__all__ = ["verify_plugin_scroll"]
