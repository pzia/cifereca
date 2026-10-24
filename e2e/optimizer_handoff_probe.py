#!/usr/bin/env python3
"""#2800: native GTK/XTEST handoff to a PREBUILT EPUB Optimizer plugin page.

The selection is an ArtifactRef from the real plugin action, not a pathname.
Only fictitious metadata and a synthetic device profile are used. No source
provider, catalog, network, file transformation or output write is performed.
This probes GTK widgets and X11 input, not a complete installed shell.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src/plugins/epub_optimizer"),
    str(ROOT / "environnements/openai-chat-debian"),
]

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from cifereca.plugin_api import ArtifactFormat, ArtifactRef, UiActionContext
from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer_plugin import EpubOptimizerPlugin
from x11_input import X11Input


def artifact(identifier: str, fmt: str):
    return ArtifactRef(
        "synthetic-" + identifier, "synthetic-book-" + identifier,
        ArtifactFormat(
            "application/pdf" if fmt == "pdf" else "application/epub+zip",
            fmt, "none",
        ),
        128, "a" * 64,
        filename="/PRIVATE/not-an-account/" + identifier + "." + fmt,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366, 1600), required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Real X11 GTK/XTEST display required")

    report = {
        "scenario": "optimizer-handoff-gtk",
        "status": "RUNNING",
        "backend": "GTK4/X11/XTEST",
        "width": args.width,
        "phase": "setup",
        "checks": {},
        "network_calls": 0,
        "writes": 0,
    }
    def save():
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    Gtk.init()
    device = X11Input()
    window = Gtk.Window(title="Cifereca synthetic optimizer handoff")
    window.set_default_size(args.width - 80, 800)
    profile = builtin_profile("xteink-x3")
    tasks = NS()  # An optimization task is NEVER submitted in this probe.
    logger = logging.getLogger("synthetic-optimizer-gtk")
    context = NS(
        config={},
        logger=logger,
        broker=lambda name: tasks if name == "tasks" else None,
        services=lambda *args, **kwargs: (
            NS(plugin_id="device.profile.xteink-x3",
               plugin_version="0.3.7", service=profile),
        ),
        subscribe_services=lambda *args, **kwargs: lambda: None,
    )
    plugin = EpubOptimizerPlugin()
    plugin.activate(context)
    selected = [None]
    navs, toasts = [], []

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    for child in (root, nav):
        child.set_margin_start(8)
        child.set_margin_end(8)
    root.append(nav)
    stack = Gtk.Stack()
    stack.set_hexpand(True)
    stack.set_vexpand(True)
    root.append(stack)

    class Host:
        def action_context(self):
            return UiActionContext(
                target="book.detail", artifact=selected[0],
                identity_id="synthetic-identity",
            )
        def navigate(self, route):
            navs.append(route)
            stack.set_visible_child_name(route)
        def toast(self, message):
            toasts.append("notice")

    host = Host()
    catalog = Gtk.Label(label="Bibliothèque — uniquement deux livres fictifs")
    stack.add_named(catalog, "library")
    plugin_page = plugin._build_page(host)
    controller = plugin._active_controller
    assert controller is not None and controller.artifact is None
    stack.add_named(plugin_page, "epub-optimize")
    action = next(a for a in plugin.pages()[0].actions
                  if a.id == "epub-optimize-current")
    a = artifact("SyntheticA", "epub")
    b = artifact("SyntheticB", "pdf")
    def button(text, callback):
        widget = Gtk.Button(label=text)
        widget.connect("clicked", lambda *_: callback())
        nav.append(widget)
        return widget

    back = button("Retour Bibliothèque",
                  lambda: stack.set_visible_child_name("library"))
    button_a = button("Optimiser A",
                      lambda: (selected.__setitem__(0, a), action.callback(host)))
    button_b = button("Optimiser B",
                      lambda: (selected.__setitem__(0, b), action.callback(host)))
    direct = button("Ouvrir page Optimiseur",
                    lambda: host.navigate("epub-optimize"))
    stack.set_visible_child_name("library")
    window.set_child(root)
    window.present()

    def until(predicate, label):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if predicate():
                return
            time.sleep(.025)
        raise AssertionError("native_" + label + "_not_observed")

    def click(widget):
        until(lambda: widget.get_mapped(), "widget_mapping")
        device.click_widget(widget)
        for _ in range(8):
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.02)

    try:
        until(lambda: window.get_mapped() and plugin_page.get_mapped() is False,
              "initial_library")
        report["checks"]["prebuilt_blank_page"] = (
            controller.artifact is None
            and not controller._artifact_info_label.get_visible()
        )
        assert report["checks"]["prebuilt_blank_page"]

        report["phase"] = "first_selection"
        click(button_a)
        until(lambda: controller.artifact is a and
              stack.get_visible_child_name() == "epub-optimize", "select_a")
        report["checks"]["action_a_to_prebuilt_page"] = (
            controller.source_entry.get_text() == "SyntheticA.epub"
            and controller.action_host is host
            and controller._artifact_info_label.get_visible()
            and not controller.source_button.get_sensitive()
        )
        assert report["checks"]["action_a_to_prebuilt_page"]

        report["phase"] = "second_selection_after_back"
        click(back)
        until(lambda: stack.get_visible_child_name() == "library", "back")
        click(button_b)
        until(lambda: controller.artifact is b and
              stack.get_visible_child_name() == "epub-optimize", "select_b")
        report["checks"]["action_b_replaces_a_without_rebuild"] = (
            plugin._active_controller is controller
            and controller.source_entry.get_text() == "SyntheticB.pdf"
            and controller.optimize_button.get_label() == "Obtenir la variante optimisée"
            and controller._artifact_info_label.get_visible()
            and not controller.source_entry.get_sensitive()
        )
        assert report["checks"]["action_b_replaces_a_without_rebuild"]

        report["phase"] = "busy_refusal"
        click(back)
        controller._task_handle = NS(done=lambda: False)
        click(button_a)
        report["checks"]["busy_refuses_stale_handoff"] = (
            controller.artifact is b
            and stack.get_visible_child_name() == "library"
            and len(toasts) == 1
        )
        assert report["checks"]["busy_refuses_stale_handoff"]
        controller._task_handle = None

        report["phase"] = "reentry"
        click(button_a)
        until(lambda: controller.artifact is a and
              stack.get_visible_child_name() == "epub-optimize", "reentry")
        report["checks"]["reentry_after_busy_uses_latest_book"] = (
            controller.source_entry.get_text() == "SyntheticA.epub"
            and controller.progress.get_text() == "Prêt"
        )
        assert report["checks"]["reentry_after_busy_uses_latest_book"]

        visible = (
            controller._artifact_info_label.get_label()
            + controller.source_entry.get_text()
            + controller.output_entry.get_text()
        )
        report["checks"]["no_private_parent_path_in_widgets"] = (
            "PRIVATE" not in visible and "not-an-account" not in visible
            and "/PRIVATE/" not in visible
        )
        assert report["checks"]["no_private_parent_path_in_widgets"]
        report["checks"]["no_optimization_started"] = (
            controller._task_handle is None and not controller.cancel_button.get_sensitive()
        )
        assert report["checks"]["no_optimization_started"]
        report["checks"]["two_exact_public_navigation_calls"] = (
            navs == ["epub-optimize"] * 3
        )
        assert report["checks"]["two_exact_public_navigation_calls"]

        report["status"] = "PASS"
        report["phase"] = "complete"
    except Exception:
        report["status"] = "FAIL"
        # Only closed phase and boolean checks enter the report; never emit
        # native exceptions (which can contain paths) into CI artifacts.
        save()
        raise AssertionError("optimizer_native_handoff_failed") from None
    finally:
        # The synthetic task is not an active Core TaskHandle.
        controller._task_handle = None
        plugin.deactivate()
        device.close()
        window.destroy()
    save()
    print("optimizer-native-handoff: PASS", flush=True)


if __name__ == "__main__":
    main()
