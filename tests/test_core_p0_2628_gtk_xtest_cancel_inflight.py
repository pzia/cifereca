"""P0 #2628: physical GTK cancel WHILE a browser install worker is active.

This is a native GTK4/Adw + XTEST test with an intentionally BLOCKED synthetic
Core installer, not an actual CDN interruption. No Chromium download, account,
provider, screenshot, network trace or user artifact is created by this test.
A separate CI recipe proves real consented Chromium download and launch.
"""
from __future__ import annotations

from threading import Event

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.plugins.configuration import PluginConfigStore
from cifereca.plugins import playwright_browser as browser
from cifereca.diagnostics import Diagnostics
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from scripts.plugin_packaging import build_plugin_zip, validate_plugin_zip
from tests.test_core_p0_2628_real_gtk_optin_chromium_e2e import (
    FakeWindow, PLUGIN_ID, SOURCE, button_named, eventually, flush,
    physical_dialog_choice, physical_page_action, visible_dialog,
)


def test_xtest_cancel_active_chromium_installer_without_touching_cache(
    monkeypatch, tmp_path,
):
    Gtk.init()
    Adw.init()

    # The exact synthetic state opens the real Chromium controls, while
    # never permitting a subprocess or real Chrome download.
    monkeypatch.setattr(
        browser, "inspect_chromium",
        lambda **_kwargs: browser.BrowserState("chromium_missing", "1.55.0"),
    )
    monkeypatch.setattr(
        "cifereca.plugins.manager.data_dir", lambda: tmp_path / "core-data",
    )
    monkeypatch.setattr(
        "cifereca.plugins.manager.cache_dir", lambda: tmp_path / "core-cache",
    )
    started = Event()
    cancelled_from_worker = Event()
    call_count = []

    def suspended_install(*, consent, cancelled, progress):
        assert consent is True
        assert cancelled is not None
        call_count.append("started")
        progress("starting")
        started.set()
        # The real GTK cancellation button must set this SAME control.
        if cancelled.wait(timeout=12):
            cancelled_from_worker.set()
            return browser.BrowserState("cancelled", "1.55.0")
        return browser.BrowserState("download_timeout", "1.55.0")

    monkeypatch.setattr(browser, "install_chromium", suspended_install)
    archive = build_plugin_zip(SOURCE, tmp_path)
    assert validate_plugin_zip(archive)["package"]["id"] == (
        "org.cifereca.technical.browser-recorder"
    )
    manager = PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
        config_store=PluginConfigStore(tmp_path / "config.json"),
        brokers={},
    )
    window = FakeWindow()
    window.diagnostics = Diagnostics()
    window.set_title("Cifereca Browser Recorder Plugins fixture")
    window.set_default_size(1366, 850)
    page = None
    try:
        manager.installer.install(archive, activate=True)
        manager.activate()
        assert not manager.status(PLUGIN_ID).enabled
        from cifereca.gui.plugin_manager_page import PluginManagerPage
        page = PluginManagerPage(window, manager)
        window.set_child(page)
        window.present()
        flush()
        assert not page._chromium_operation_running
        assert window.diagnostics.snapshot() == []  # no passive diagnostic

        # XTEST opens and confirms the REAL native dialog, not Gtk.emit.
        install_button = button_named(
            page._plugin_widgets[PLUGIN_ID], "Installer Chromium…"
        )
        physical_page_action(window, install_button)
        assert eventually(lambda: visible_dialog() is not None)
        dialog = visible_dialog()
        assert dialog.get_heading() == "Installer Chromium avec Playwright ?"
        assert call_count == []
        assert window.diagnostics.snapshot() == []
        physical_dialog_choice(dialog, "Installer")
        assert eventually(lambda: started.is_set())
        assert eventually(lambda: page._chromium_operation_running)
        assert not cancelled_from_worker.is_set()

        # The Core keeps this button on the real Plugins page while busy.
        abort = button_named(page._plugin_widgets[PLUGIN_ID], "Annuler Chromium")
        assert abort.get_sensitive()
        physical_page_action(window, abort)
        assert eventually(lambda: cancelled_from_worker.is_set())
        assert eventually(lambda: not page._chromium_operation_running)
        assert call_count == ["started"]
        assert page._chromium_cancel.is_set()
        public = query_diagnostic_events(
            window.diagnostics, log_path=tmp_path / "absent",
            source="core_events", module="PLUGIN", severity="warning",
        )
        assert public["total"] == 1
        assert public["events"][0]["detail"] == {
            "plugin_id": "ui.page.browser-recorder",
            "operation": "chromium_install",
            "code": "cancelled",
        }

        result = visible_dialog()
        assert result is not None
        assert result.get_heading() == "État Chromium"
        assert "annul" in result.get_body().lower()
        physical_dialog_choice(result, "Fermer")
        assert not manager.status(PLUGIN_ID).enabled
        assert not page._chromium_operation_running
        assert not list(tmp_path.glob("**/ms-playwright"))
        assert not any(
            "traceback" in str(entry).lower() for entry in window.messages
        )
    finally:
        if page is not None:
            page._chromium_cancel.set()
            eventually(lambda: not page._chromium_operation_running, seconds=12)
        window._closed = True
        window.close()
        manager.close()
        flush()
