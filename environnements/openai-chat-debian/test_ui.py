#!/usr/bin/env python3
"""Integration proof: real GTK widgets, real XTEST input, native GTK PNGs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import gi
from app import DemoApplication, Gtk, GLib
from native_capture import capture
from x11_input import X11Input


def pump(seconds=0.25):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def tree(widget, root):
    success, rect = widget.compute_bounds(root)
    result = {
        "type": widget.__gtype__.name,
        "name": widget.get_name(),
        "visible": widget.get_visible(),
        "mapped": widget.get_mapped(),
        "sensitive": widget.get_sensitive(),
        "bounds": [round(rect.get_x(), 2), round(rect.get_y(), 2), round(rect.get_width(), 2), round(rect.get_height(), 2)] if success else None,
    }
    if isinstance(widget, Gtk.Label):
        result["text"] = widget.get_text()
    if isinstance(widget, Gtk.Editable):
        result["text"] = widget.get_text()
    children = []
    child = widget.get_first_child()
    while child is not None:
        children.append(tree(child, root))
        child = child.get_next_sibling()
    result["children"] = children
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evidence"))
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "pygobject": gi.__version__,
        "gtk": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
        "display": os.environ.get("DISPLAY"),
        "input_backend": "X11 XTEST: pointer movement, button and keyboard events",
        "tests": [], "actions": [], "captures": [],
    }

    def check(name, condition, evidence):
        report["tests"].append({"name": name, "passed": bool(condition), "evidence": evidence})
        if not condition:
            raise AssertionError(f"{name}: {evidence}")

    def shot(window, name):
        filename = out / name
        info = capture(window, filename)
        info["sha256"] = hashlib.sha256(filename.read_bytes()).hexdigest()
        from PIL import Image
        with Image.open(filename) as image:
            image.load()
            info["pixel_size"] = list(image.size)
            info["color_extrema"] = image.convert("RGB").getextrema()
        report["captures"].append(info)
        return filename

    app = DemoApplication()
    input_device = None
    try:
        check("actual_gtk4_import", Gtk.get_major_version() == 4, report["gtk"])
        app.register(None)
        app.activate()
        pump(0.6)
        window = app.window
        check("window_mapped", window is not None and window.get_mapped(), {"width": window.get_width(), "height": window.get_height()})
        check("initial_catalogue", len(window.visible_books) == 4, window.state())
        initial = shot(window, "01-initial.png")
        (out / "widgets-initial.json").write_text(json.dumps(tree(window, window), ensure_ascii=False, indent=2) + "\n")
        check("native_software_renderer", window.get_renderer().__gtype__.name == "GskCairoRenderer", report["captures"][0])

        input_device = X11Input()
        report["actions"].append(input_device.click_widget(window.available))
        pump()
        check("mouse_available_filter", window.available.get_active() and len(window.visible_books) == 3, window.state())
        report["actions"].append(input_device.click_widget(window.search))
        pump(0.1)
        input_device.type_text("tour")
        report["actions"].append({"keyboard": "tour", "source": "XTEST key press/release events"})
        pump(0.6)
        check("keyboard_search", window.search.get_text() == "tour" and len(window.visible_books) == 1 and "Tour" in window.visible_books[0].title, window.state())
        report["actions"].append(input_device.click_widget(window.transfer))
        pump()
        check("mouse_transfer_button", window.transfers == 1 and "terminé" in window.status.get_text(), window.state())
        interacted = shot(window, "02-after-interaction.png")
        report["state_after_interaction"] = window.state()
        (out / "widgets-after.json").write_text(json.dumps(tree(window, window), ensure_ascii=False, indent=2) + "\n")
        check("image_changed_after_input", hashlib.sha256(initial.read_bytes()).digest() != hashlib.sha256(interacted.read_bytes()).digest(), "Initial and filtered/status snapshots differ")

        report["actions"].append(input_device.click_widget(window.search))
        input_device.select_all_delete()
        input_device.type_text("zzzz")
        pump(0.6)
        check("empty_search_disables_transfer", len(window.visible_books) == 0 and not window.transfer.get_sensitive(), window.state())
        shot(window, "03-no-results.png")
        before = window.transfers
        report["actions"].append(input_device.click_widget(window.transfer))
        pump()
        check("disabled_button_ignores_mouse", window.transfers == before, window.state())

        input_device.click_widget(window.search)
        input_device.select_all_delete()
        input_device.type_text("tour")
        pump(0.6)
        shot(window, "gtk4-proof.png")
        check("png_is_valid_and_nonblank", all(c["width"] > 500 and c["height"] > 300 and any(high > low for low, high in c["color_extrema"]) for c in report["captures"]), "Decoded all PNGs and checked pixel dimensions/color ranges")
        report["final_state"] = window.state()
        report["result"] = "PASS"
        return_code = 0
    except Exception as error:
        report["result"] = "FAIL"
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
        return_code = 1
    finally:
        if input_device:
            input_device.close()
        if app.window:
            app.window.destroy()
        (out / "test-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    passed = sum(test["passed"] for test in report["tests"])
    print(f"GTK4 {report['gtk']} / Python {report['python']} / PyGObject {report['pygobject']}")
    print(f"{report['result']}: {passed}/{len(report['tests'])} checks; report: {out / 'test-report.json'}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
