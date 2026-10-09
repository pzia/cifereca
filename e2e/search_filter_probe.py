#!/usr/bin/env python3
"""P0 #660: replay real Search filters in the running CIFERECA GTK window.

The XTEST input targets the actual GTK widgets. No fake QueryControls, fake
search service, external account or provider network is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environnements" / "openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gio, Gtk

from PIL import ImageGrab
from x11_input import X11Input
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("DISPLAY") or os.environ.get("GDK_BACKEND") != "x11":
        raise SystemExit("GTK/Xvfb X11 requis")

    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=4,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        sha = "unknown"

    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    for i in range(36):
        # A loaded local index with two sources, two formats and two identities.
        core.storage.catalog_entry_put({
            "catalog_entry_id": f"p0-camille-{i:03d}",
            "title": f"Camille et les livres {i:03d}",
            "author": "Autrice E2E",
            "identity_id": "camille",
            "provider_id": "gallica" if i % 2 else "local",
            "item_id": f"camille-{i:03d}",
            "local_state": "remote_catalog",
            "media_format": "epub" if i % 3 else "pdf",
            "metadata": {"medium": "digital", "language": "fr"},
        })
    for i in range(4):
        core.storage.catalog_entry_put({
            "catalog_entry_id": f"p0-alex-{i:03d}",
            "title": f"Camille, livre privé {i}",
            "identity_id": "alex", "provider_id": "local",
            "item_id": f"alex-{i}", "local_state": "remote_catalog",
            "media_format": "epub", "metadata": {"medium": "digital"},
        })

    result = {
        "scenario": "search-filter-p0", "git_sha": sha,
        "backend": "GTK4/X11/Xvfb", "identities": ["camille", "alex"],
        "steps": [], "checks": {"query": True, "rows": True, "count": True,
                               "selection": True, "scroll": True, "search_requests": True},
    }
    capture = None
    window = None
    device = None
    baseline = None
    started = monotonic()
    search_requests = [0]
    rc = [1]
    actions = [
        ("source", lambda: window.search_controls.provider),
        ("format", lambda: window.search_controls.types),
        ("language", lambda: window.search_controls.languages),
        ("field", lambda: window.search_controls.field),
        ("support", lambda: window.search_controls.scope),
    ]

    original_search = core.library.search_known_catalog

    def counted_search(*a, **kw):
        search_requests[0] += 1
        return original_search(*a, **kw)

    core.library.search_known_catalog = counted_search

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.SearchFiltersP0")

        def do_activate(self):
            nonlocal window, device
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            window.present()
            device = X11Input()
            later(600, start_search)

        def do_shutdown(self):
            if device is not None:
                device.close()
            core.close()
            Gio.Application.do_shutdown(self)

    app = Application()

    def later(ms, callback):
        def run():
            try:
                callback()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, run)

    def capture_stage(stage: str, *, popup: bool = False):
        nonlocal capture
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        path = output / f"{stage}.png"
        image.save(path)
        capture = path
        state = snapshot()
        result["steps"].append({
            "stage": stage, "popup_expected": popup,
            "screenshot": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            **state,
        })
        if popup and image.convert("RGB").getcolors(maxcolors=2) is not None:
            raise AssertionError("Capture GTK vide après ouverture filtre")

    def snapshot():
        page = window.search_page
        selected = page.selection.get_selected_item()
        adjust = page.scroll.get_vadjustment()
        return {
            "query": window.search_controls.entry.get_text(),
            "count": page.count_label.get_label(),
            "rows": [page.store.get_item(i).key for i in range(page.store.get_n_items())],
            "selected": getattr(selected, "key", None),
            "scroll": round(float(adjust.get_value()), 2),
            "search_requests": search_requests[0],
            "route": window.view_stack.get_visible_child_name(),
        }

    def check(stage):
        state = snapshot()
        if baseline is None:
            raise AssertionError("Recherche non initialisée avant filtre")
        for key in ("query", "rows", "count", "selected", "search_requests", "route"):
            if state[key] != baseline[key]:
                result["checks"][{"query": "query", "rows": "rows", "count": "count",
                                  "selected": "selection", "search_requests": "search_requests",
                                  "route": "route"}.get(key, key)] = False
                raise AssertionError(f"{stage}: {key} modifié par ouverture/fermeture du filtre")
        if abs(state["scroll"] - baseline["scroll"]) > 2:
            result["checks"]["scroll"] = False
            raise AssertionError(f"{stage}: scroll modifié par le filtre")

    def start_search():
        window.navigate("search")
        controls = window.search_controls
        device.click_widget(controls.entry)  # real XTEST mouse click
        device.type_text("Camille")           # real keyboard events
        device.key("Return", True)
        device.key("Return", False)
        later(200, wait_results)

    def wait_results():
        if monotonic() - started > 20:
            raise AssertionError("Recherche locale non chargée sous GTK")
        state = snapshot()
        if len(state["rows"]) < 30 or search_requests[0] < 1:
            later(150, wait_results)
            return
        if len(state["rows"]) != 36 or state["query"] != "Camille":
            raise AssertionError(f"Fixture locale inattendue : {len(state['rows'])} résultats")
        page = window.search_page
        page.selection.set_selected(3)
        later(250, setup_scroll)

    def setup_scroll():
        nonlocal baseline
        page = window.search_page
        adjustment = page.scroll.get_vadjustment()
        room = adjustment.get_upper() - adjustment.get_page_size()
        if room <= 20:
            raise AssertionError("Liste trop courte pour valider le scroll GTK")
        adjustment.set_value(min(120, room / 2))
        later(250, set_baseline)

    def set_baseline():
        nonlocal baseline
        baseline = snapshot()
        if (baseline["count"] != "36 résultats · Connu"
                or baseline["selected"] is None
                or baseline["search_requests"] != 1
                or baseline["scroll"] < 5):
            raise AssertionError(f"Avant filtre non probant : {baseline}")
        capture_stage("00-before")
        next_filter(0)

    def next_filter(index: int):
        if index == len(actions):
            finish()
            return
        name, widget_factory = actions[index]
        widget = widget_factory()
        widget.grab_focus()
        # Wait for GTK to scroll the horizontal filter toolbar into view.
        later(170, lambda: open_filter(index, name, widget))

    def open_filter(index, name, widget):
        result["steps"].append({
            "stage": f"{name}-click-open",
            "input": device.click_widget(widget),
        })
        later(220, lambda: observe_open(index, name, widget))

    def observe_open(index, name, widget):
        check(f"{name} ouvert")
        if isinstance(widget, Gtk.MenuButton) and not widget.get_popover().get_mapped():
            raise AssertionError(f"{name}: popover non ouvert via XTEST")
        capture_stage(f"{index+1:02d}-{name}-open", popup=True)
        device.click_widget(widget)
        later(220, lambda: observe_closed(index, name, widget))

    def observe_closed(index, name, widget):
        check(f"{name} fermé")
        if isinstance(widget, Gtk.MenuButton) and widget.get_popover().get_mapped():
            raise AssertionError(f"{name}: popover non fermé via XTEST")
        capture_stage(f"{index+1:02d}-{name}-close")
        next_filter(index + 1)

    def finish():
        result["status"] = "PASS"
        result["duration_seconds"] = round(monotonic() - started, 2)
        result["before"] = baseline
        if capture is not None:
            shutil.copyfile(capture, output / "screen.png")
        write_report()
        rc[0] = 0
        app.quit()

    def fail(message):
        result["status"] = "FAIL"
        result["error"] = message
        result["duration_seconds"] = round(monotonic() - started, 2)
        if capture is not None:
            shutil.copyfile(capture, output / "screen.png")
        write_report()
        app.quit()

    def write_report():
        (output / "screen.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    GLib.timeout_add_seconds(30, lambda: (fail("timeout GTK P0 (30s)"), False)[1] if rc[0] else False)
    app.run([sys.argv[0]])
    if not (output / "screen.json").is_file():
        result["status"] = "FAIL"
        result["error"] = "application GTK fermée avant preuve"
        write_report()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
