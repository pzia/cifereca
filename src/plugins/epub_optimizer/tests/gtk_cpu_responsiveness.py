"""Explicit GTK integration: real OptimizerPage + Core tasks + actual 1/2/4 spawn.

Synthetic fixtures and structural profile context only; the page, GTK main
loop, Core task broker, progress delivery and transformations are real.
This is not a full installed-application E2E or a user's hardware recipe.
Run under the repository's existing headless GTK environment.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "src"), os.environ.get("CIFERECA_GTK_PACKAGE_ROOT", str(Path(__file__).resolve().parents[1]))]


def main():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    from cifereca.task_runtime import TaskBrokerImpl
    from cifereca_epub_optimizer.builtin_profiles import builtin_profile
    from cifereca_epub_optimizer.ui import OptimizerPage
    from benchmark_optimizer_cpu import fixtures, semantic_digest
    Gtk.init()
    tasks = TaskBrokerImpl(None, max_workers=1)
    profile = builtin_profile("xteink-x3")
    results, signatures = [], {}
    context = SimpleNamespace(
        config={}, logger=logging.getLogger("optimizer-gtk-test"),
        broker=lambda name: tasks.for_plugin("ui.page.epub-optimizer") if name == "tasks" else None,
        services=lambda *args, **kw: (SimpleNamespace(
            plugin_id="device.profile.xteink-x3", plugin_version="0.3.7", service=profile),),
        subscribe_services=lambda *args: lambda: None,
    )
    window = Gtk.Window(title="Cifereca Optimizer integration")
    window.set_default_size(1000, 760)
    messages = []
    try:
        with tempfile.TemporaryDirectory(prefix="optimizer-gtk-") as td:
            root = Path(td)
            fixtures(root, heavy_images=8, width=1000, height=1500)
            for fmt in ("epub", "pdf"):
                source = root / f"heavy.{fmt}"
                before = source.read_bytes()
                for workers in ((1,) if os.environ.get("CIFERECA_GTK_BASELINE") else (1, 2, 4)):
                    context.config = {"cpu_workers": workers}
                    page = OptimizerPage(host=SimpleNamespace(toast=messages.append), context=context)
                    window.set_child(page.widget)
                    window.present()
                    page.source_entry.set_text(str(source))
                    page.output_entry.set_text(str(root / f"out-{workers}.{fmt}"))
                    page.profile_combo.set_active_id("device.profile.xteink-x3")
                    gaps, last = [], [time.perf_counter()]
                    def heartbeat():
                        now = time.perf_counter()
                        gaps.append((now - last[0]) * 1000)
                        last[0] = now
                        return True
                    heartbeat_id = GLib.timeout_add(10, heartbeat)
                    page._start(None)
                    handle = page._task_handle
                    assert handle is not None
                    deadline = time.monotonic() + 45
                    while page._task_handle is not None and time.monotonic() < deadline:
                        GLib.MainContext.default().iteration(True)
                    GLib.source_remove(heartbeat_id)
                    assert handle.done(), "GTK optimization did not finish"
                    report = handle.result()
                    assert not report.image_errors
                    assert source.read_bytes() == before
                    assert page.progress.get_fraction() == 1
                    assert not page.cancel_button.get_sensitive()
                    assert len(gaps) >= 3, "main loop did not run during optimization"
                    # A bounded responsiveness guard, not a claim of identical
                    # frame latency across machines or a user's GTK session.
                    assert max(gaps) < 500, f"GTK main-loop stall: {max(gaps)} ms"
                    signature = semantic_digest(Path(report.output_path))
                    if fmt in signatures: assert signature == signatures[fmt]
                    signatures[fmt] = signature
                    ordered = sorted(gaps)
                    results.append({"format": fmt, "workers": workers,
                        "heartbeat_count": len(gaps), "p50_gap_ms": statistics.median(gaps),
                        "p95_gap_ms": ordered[max(0, int(len(gaps)*.95)-1)], "max_gap_ms": max(gaps),
                        "images_optimized": report.images_optimized})
                    page._unsubscribe_profiles()
            print(json.dumps({"status": "PASS", "origin": "synthetic_gtk_integration",
                              "full_application_e2e": False, "results": results}, indent=2))
    finally:
        tasks.close()
        window.destroy()


if __name__ == "__main__":
    main()
