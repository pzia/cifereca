"""P0 #2141: account cards remain scoped and visually separate."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.shell_stylesheet_controller import install_css
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def test_one_panel_per_account_with_accessible_source_heading():
    window, broker, calls, _build, _page = _fixture(sources=[
        _source(source_id="kobo-one", label="Kobo"),
        _source(source_id="numilog-one", provider_id="numilog",
                label="Numilog", sync_capabilities=("remote_inventory",)),
    ])
    panels = window._sync_sources_box.children
    assert len(panels) == 2
    assert all("sync-source-panel" in panel.css_classes for panel in panels)
    assert [panel.children[0].title for panel in panels] == ["Kobo", "Numilog"]
    assert all("sync-source-heading" in panel.children[0].css_classes
               for panel in panels)
    assert calls == [("sources", "alice")]
    assert window.runner.tasks == []


def test_css_rule_is_limited_to_source_panels_and_headings():
    captured = []

    class Provider:
        def load_from_data(self, css):
            captured.append(css)

    fake_gtk = NS(
        CssProvider=Provider,
        StyleContext=NS(add_provider_for_display=lambda *_args: None),
        STYLE_PROVIDER_PRIORITY_APPLICATION=600,
    )
    fake_gdk = NS(Display=NS(get_default=lambda: object()))
    install_css(NS(_closed=False), Gtk=fake_gtk, Gdk=fake_gdk)
    assert len(captured) == 1
    assert b".sync-source-panel {" in captured[0]
    assert b"border: 1px solid @borders;" in captured[0]
    assert b".sync-source-heading .title" in captured[0]
