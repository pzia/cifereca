#!/usr/bin/env python3
"""P1 #757: real MainWindow GTK4 light/dark visual capture, offline synthetic UI.

This probe creates *private* PNGs and an assertion report under e2e-artifacts.
GitHub Actions exports only its redacted closed verdict, never the screenshots.
It is a regression gate for theme wiring, not a substitute for human UX review.
"""
from __future__ import annotations

from dataclasses import replace
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from zipfile import ZIP_DEFLATED, ZipFile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageChops, ImageGrab, ImageStat

from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow
from cifereca.gui.modern_light_theme import MODERN_LIGHT_CLASS

ROOT = Path(__file__).resolve().parents[1]
# The plugin under test is actual shipped plugin code, not a synthetic GTK
# stand-in. Its context comes from the existing offline Constellations E2E.
sys.path.insert(0, str(ROOT / "src/plugins/constellations"))
from constellations_page import ConstellationsPlugin
from constellations_detail_probe import Context as ConstellationsFixtureContext

ROUTES = ("dashboard", "search", "library", "loans", "sync", "plugins", "settings", "constellations")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(1366, 1600))
    args = parser.parse_args()
    # Closed diagnostic only: no exception text, paths, account or plugin data
    # in CI logs. Early setup errors previously produced only a generic FAIL.
    phase = ["preflight"]
    def closed_exception(_type, _value, _traceback):
        print("P1_757_CLOSED_FAILURE_STAGE=" + phase[0], flush=True)
    sys.excepthook = closed_exception
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Vrai GTK4 sous Xvfb/X11 requis")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Never read/write the operator's default config, identities or SQLite.
    # The synthetic book must not survive a local UX rehearsal.
    scratch = TemporaryDirectory(prefix="cifereca-ux757-")
    work = Path(scratch.name)
    # The app's auxiliary paths (book directory, diagnostics, cache and
    # plugin state) also derive from HOME/XDG. Keep all of them disposable,
    # without changing the user's process environment outside this probe.
    sandbox_home = work / "home"
    sandbox_home.mkdir()
    os.environ["HOME"] = str(sandbox_home)
    for name, folder in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
    ):
        os.environ[name] = str(work / folder)
    os.environ["CIFERECA_PLUGIN_DEPS_OFFLINE"] = "1"
    # The local search fixture belongs to Camille. Configure that identity
    # inside the disposable HOME before LibraryService snapshots identities:
    # the default 'default' identity cannot see Camille's catalog records.
    (work / "config.json").write_text(
        json.dumps({
            "identities": [{
                "id": "camille",
                "label": "Camille (fixture E2E)",
                "preferences": {"reading_languages": ["fr"]},
            }],
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    phase[0] = "core_open"
    core = CiferecaCore.open(
        config_path=work / "config.json",
        database_path=work / "library.sqlite",
        no_plugins=False, force_plugins=("ui.page.home",), activate_plugins=False,
    )
    assert core.library.current_identity is not None
    assert core.library.current_identity.identity_id == "camille", (
        "Identité active divergente de la notice synthétique"
    )
    # Install the actual bundled Home page package through the production
    # package manager, but only into our disposable sandbox. It must outrank
    # the builtin dashboard; otherwise this is not an installed-plugin test.
    source = ROOT / "src/plugins/home-page"
    archive = work / "home-page.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        for name in ("cifereca-plugin.json", "cifereca_home.py"):
            bundle.write(source / name, arcname=name)
    phase[0] = "plugin_install"
    installed = core.plugins.install(archive)
    assert installed.manifest.package.id == "org.cifereca.ui.page.home"
    assert core.plugins.packages.active_version(installed.manifest.package.id) == (
        installed.manifest.package.version
    )
    phase[0] = "plugin_activation"
    real_pages = core.plugins.page_contributions
    home = next((
        page for page in real_pages() if page.route == "dashboard"
        and getattr(getattr(page.factory, "__self__", None), "__class__", type(None)).__name__
        == "HomePlugin"
    ), None)
    assert home is not None, "La page Accueil installée n'est pas active"
    # A second real shipped plugin page uses the existing offline context,
    # with no remote provider, network call, or persistent user account.
    phase[0] = "constellations_setup"
    plugin = ConstellationsPlugin().activate(ConstellationsFixtureContext())
    core.plugins.page_contributions = lambda: [*real_pages(), *plugin.pages()]
    # Identity and publication data belong only to the isolated E2E fixture.
    phase[0] = "fixture_storage"
    core.storage.catalog_entry_put({
        "catalog_entry_id": "ux757-book", "item_id": "ux757-book",
        "identity_id": "camille", "provider_id": "local",
        "title": "Exemple de détail UX", "author": "Autrice fictive",
        "local_state": "remote_catalog", "media_format": "epub",
        "metadata": {"description": "Un livre synthétique pour la recette GTK."},
    })
    manager = Adw.StyleManager.get_default()
    original_scheme = manager.get_color_scheme()
    manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    report = {
        "status": "RUNNING", "width": args.width, "backend": "GTK4/X11",
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "checks": {}, "screenshots": [],
        "scope": "MainWindow GTK réelle, plugin Constellations réel hébergé, identité et données synthétiques hors ligne",
    }
    state = {"route": 0, "phase": "start", "deadline": monotonic() + 50}
    active = {"window": None, "light_image": None, "app": None}
    returncode = [1]

    def save():
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def capture(route: str, appearance: str):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).convert("RGB")
        assert image.size == (args.width, 1000 if args.width == 1600 else 900)
        assert image.getbbox() is not None, "Capture GTK vide"
        name = f"{len(report['screenshots']):02d}-{route}-{appearance}.png"
        target = out / name
        image.save(target)
        shutil.copyfile(target, out / "screen.png")
        report["screenshots"].append({
            "stage": route, "appearance": appearance, "file": name,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })
        return image

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        # Public CI export drops this detail; raw reports stay private.
        report["status"] = "FAIL"
        print("P1_757_CLOSED_FAILURE_STAGE=" + phase[0], flush=True)
        report["error"] = f"{type(exc).__name__}: {exc}"
        save()
        active["app"].quit()

    def later(delay: int, action):
        def callback():
            if report["status"] != "RUNNING":
                return False
            try:
                if monotonic() > state["deadline"]:
                    raise AssertionError("Recette GTK dépassant son délai borné")
                action()
            except Exception as exc:
                fail(exc)
            return False
        GLib.timeout_add(delay, callback)

    def check_appearance(light: bool):
        w = active["window"]
        expected = not light
        assert bool(manager.get_dark()) is expected, "Mode Adwaita non appliqué"
        assert w.has_css_class(MODERN_LIGHT_CLASS) is light, (
            "Classe V2 non synchronisée avec Adwaita"
        )
        assert w.get_mapped() and w.get_width() > 500, "Fenêtre GTK non rendue"
        assert w._modern_light_css_installed is True
        assert w.get_surface() is not None

    def start_route():
        phase[0] = "route_" + (ROUTES[state["route"]] if state["route"] < len(ROUTES) else "finish")
        if state["route"] >= len(ROUTES):
            report["checks"]["P1_757_all_routes_light_dark_gtk"] = True
            manager.set_color_scheme(original_scheme)
            report["status"] = "PASS"
            save()
            returncode[0] = 0
            active["app"].quit()
            return
        route = ROUTES[state["route"]]
        w = active["window"]
        assert route in w._pages, "Route GTK absente : " + route
        if route == "dashboard":
            owner = getattr(w._pages[route].factory, "__self__", None)
            assert owner is getattr(home.factory, "__self__", None), (
                "La page Accueil du package installé n'est pas sélectionnée"
            )
            assert isinstance(w._plugin_page_widgets[route], Gtk.ScrolledWindow)
            report["checks"]["P1_757_real_installed_home_plugin"] = True
        if route == "constellations":
            assert plugin.view is not None, "Plugin Constellations non construit"
            assert w._plugin_page_widgets.get(route) is plugin.view.widget, (
                "La page plugin réelle n'est pas hébergée par MainWindow"
            )
            report["checks"]["P1_757_real_plugin_factory_hosted"] = True
        if route == "library":
            w._library_entries_cache = [{
                "catalog_entry_id": "ux757-book", "item_id": "ux757-book",
                "identity_id": "camille", "provider_id": "local",
                "key": "ux757-book", "title": "Exemple de détail UX",
                "author": "Autrice fictive", "media_format": "epub",
                "metadata": {},
            }]
        w.navigate(route)
        assert w.view_stack.get_visible_child_name() == route, (
            "Route GTK non affichée : " + route
        )
        if route == "search":
            w.search_controls.set_state(
                replace(w.search_controls.state(), text="Exemple de détail UX",
                        field="title", scope="all"),
                notify=False,
            )
            w.run_search(False)
            later(320, await_search)
        else:
            later(260, light_route)

    def await_search():
        w = active["window"]
        if w.search_page.store.get_n_items() != 1:
            later(200, await_search)
            return
        light_route()

    def light_route():
        route = ROUTES[state["route"]]
        w = active["window"]
        assert w.view_stack.get_visible_child_name() == route
        check_appearance(True)
        page = w.view_stack.get_visible_child()
        assert page is not None and page.get_mapped() and page.get_width() > 0
        # A mapped page cannot silently request a width beyond the GTK stack.
        # This catches gross content overflow, not sub-widget text clipping.
        assert 0 < page.get_width() <= w.view_stack.get_width() + 2, (
            "Page GTK plus large que sa zone de navigation"
        )
        assert w.get_width() <= args.width and w.get_height() <= (
            1000 if args.width == 1600 else 900
        ), "Fenêtre GTK dépassant l'écran de recette"
        report["checks"][f"P1_757_viewport_bounds_{route}"] = True
        if route == "constellations":
            assert plugin.view.widget.get_root() is w
            report["checks"]["P1_757_plugin_light_real_gtk"] = True
        row = w.nav_rows.get(route)
        if row is not None and row.get_mapped():
            # Confirm focus really entered the live GTK application. Calling
            # grab_focus() alone is not proof of keyboard focus propagation.
            assert row.grab_focus(), "Ligne de navigation non focalisable"
            focused = w.get_focus()
            assert focused is not None and focused.get_root() is w, (
                "Focus clavier absent de la fenêtre GTK"
            )
            report["checks"][f"P1_757_navigation_focus_{route}"] = True
        light = capture(route, "light")
        active["light_image"] = light
        # Explicitly exercise the live libadwaita switch, rather than mocking
        # style manager values or GTK CSS classes on a fake window.
        manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        later(240, dark_route)

    def dark_route():
        route = ROUTES[state["route"]]
        check_appearance(False)
        if route == "constellations":
            assert plugin.view.widget.get_mapped()
            report["checks"]["P1_757_plugin_dark_real_gtk"] = True
        dark = capture(route, "dark")
        light = active["light_image"]
        delta = sum(ImageStat.Stat(ImageChops.difference(light, dark)).mean) / 3
        assert delta > 2.0, "Aucune variation visuelle claire/sombre"
        report["checks"][f"P1_757_dark_transition_{route}"] = True
        # This is a screen-wide pixel metric, not a measured text contrast.
        report.setdefault("mean_rgb_delta", {})[route] = round(delta, 2)
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        later(240, restore_route)

    def restore_route():
        route = ROUTES[state["route"]]
        check_appearance(True)
        report["checks"][f"P1_757_light_restore_{route}"] = True
        if route == "search":
            w = active["window"]
            assert w.search_page.store.get_n_items() == 1
            w.search_page.selection.set_selected(0)
            later(260, capture_search_detail)
            return
        state["route"] += 1
        later(90, start_route)

    def capture_search_detail():
        w = active["window"]
        assert w.search_page.selection.get_selected_item() is not None
        assert w.search_page.detail.get_mapped()
        check_appearance(True)
        capture("search-detail", "light")
        report["checks"]["P1_757_real_search_detail_light"] = True
        manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        later(240, capture_search_detail_dark)

    def capture_search_detail_dark():
        check_appearance(False)
        capture("search-detail", "dark")
        report["checks"]["P1_757_real_search_detail_dark"] = True
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        state["route"] += 1
        later(240, start_route)

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.ModernLightUX757")

        def do_activate(self):
            try:
                w = MainWindow(self, core.config, core.library, core.diagnostics)
                active["window"] = w
                w.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
                w.present()
                later(420, start_route)
            except Exception as exc:
                fail(exc)

        def do_shutdown(self):
            # Each shutdown phase runs even if plugin code raises; remove the
            # disposable HOME/XDG roots *after* GTK and Core have closed.
            try:
                manager.set_color_scheme(original_scheme)
                plugin.deactivate()
            finally:
                try:
                    core.close()
                finally:
                    try:
                        Gio.Application.do_shutdown(self)
                    finally:
                        scratch.cleanup()

    phase[0] = "gtk_activation"
    app = Application()
    active["app"] = app
    GLib.timeout_add_seconds(
        55, lambda: (fail(TimeoutError("GTK thème #757: watchdog")), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report["status"] = "FAIL"
        report["error"] = "Fermeture GTK sans rapport"
        save()
    return returncode[0]


if __name__ == "__main__":
    raise SystemExit(main())
