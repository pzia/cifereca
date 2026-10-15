#!/usr/bin/env python3
"""UX #1011: navigate from the real Cifereca Library shell to Diagnostics via XTEST.

Unlike diagnostics_page_probe, this mounts the real Diagnostics page contribution
in the real MainWindow. The plugin broker is deliberately local and deterministic;
this does not exercise ZIP installation or a live provider.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import statistics
import time
import subprocess
import sys
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/plugins/diagnostics-page"))
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab

from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow, BookListItem, fr_date
from cifereca.gui.recent_time import format_recent_timestamp
from cifereca.builtin_ui.cifereca_builtin_ui import Pages
from diagnostics_page_probe import Broker, Context, Host, Tasks
from diagnostics_states_probe import StateTasks
from x11_input import X11Input


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


class SlowNavigationBroker(Broker):
    """Slow task only when explicitly clicked; never block GTK's main loop."""

    def active_checks(self):
        self.checks += 1
        sleep(1.15)
        return [{"status": "ok", "message": "Contrôle local fictif terminé"}]


class IsolatedContributions:
    """Real page contributions, no installed packages and no network providers."""

    def __init__(self, diagnostics_page):
        self.diagnostics_page = diagnostics_page

    def page_contributions(self):
        return [*Pages().pages(), *self.diagnostics_page.pages()]

    def statuses(self):
        return []

    def diagnostic(self):
        return []

    def package_inventory(self, *, include_expected=False):
        return []

    def widget_catalog(self):
        return None

    def select(self, _capability):
        return None

    def services(self, _capability):
        return ()



# Frozen #1129/U0 oracle: main_window.py blob
# 6ec09453f1495ac0415ef27e0f4fa31535af282d. Never called by product code.
def baseline_render_library(self, entries: list[dict[str, Any]] | None = None) -> None:
        if not hasattr(self, "library_page"):
            return
        _perf_started = time.perf_counter()
        identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
        if entries is None:
            entries = list(self._library_entries_cache)
        self._library_entries_cache = list(entries)
        rows: list[BookListItem] = []
        self._library_objects = {}
        for item in self.library_controls.state().filter(entries):
            provider = str(item.get("provider_id") or "local")
            providers = list(item.get("providers") or [provider])
            provider_label = (f"{len(providers)} sources" if len(providers) > 1 else self.service.provider_display_name(provider))
            state = self._library_availability_label(item)
            key = str(item.get("key") or f"library:{len(self._library_objects)}")
            self._library_objects[key] = item
            metadata = item.get("metadata") or {}
            meta_parts = [state] if state else []
            if item.get("due_at"):
                meta_parts.append(f"retour {fr_date(str(item.get('due_at')))}")
            sync_status = item.get("sync_status")
            if sync_status and not item.get("local"):
                meta_parts.append(self._kobo_backup_status_label(str(sync_status)))
            device_statuses = item.get("device_statuses") if isinstance(item.get("device_statuses"), list) else []
            if device_statuses:
                present_count = sum(1 for status_row in device_statuses if status_row.get("observed_state") == "present")
                keep_count = sum(1 for status_row in device_statuses if status_row.get("desired_state") == "keep")
                if present_count:
                    meta_parts.append(f"Kobo {present_count}/{len(device_statuses)}")
                elif keep_count:
                    meta_parts.append("Kobo à synchroniser")
            rows.append(BookListItem(
                key, str(item.get("title") or "Sans titre"), str(item.get("author") or ""),
                " · ".join(part for part in meta_parts if part), provider_label, item.get("thumbnail_url"),
            ))
        self._attach_row_facets(rows, self._library_objects, self.library_controls)
        self.library_page.show_rows(rows, total_count=len(entries))
        restored_pending = self._select_pending_library_item()
        if not restored_pending and self.library_page.selection.get_selected_item() is None:
            self._library_selected(None)
        self.ui_perf.record("render_library", (time.perf_counter() - _perf_started) * 1000.0)


def compare_library_render(window):
    """Compare the real GTK rebuild against frozen U0, attributing its phases.

    Timings are inclusive, measured on both paths in the same process, widget
    tree, row volume and alternating order. Interception is confined to this
    E2E fixture; it does not modify production UI or instrument every row.
    GTK show_rows also includes native ListStore signals/callbacks, while
    selected callbacks may be nested inside it. Do not add inclusive timings
    to claim an exclusive sum. No wall-time success gate is inferred.
    """
    import gc

    entries = list(window._library_entries_cache)
    assert len(entries) == 563
    samples = {"baseline_u0": [], "extracted": []}
    phases = ("row_constructor", "facets", "gtk_show_rows", "pending_selection", "selection_callback")
    phase_samples = {
        name: {phase: [] for phase in phases}
        for name in samples
    }
    phase_calls = {name: {phase: [] for phase in phases} for name in samples}
    gc_collections = {name: [] for name in samples}
    cpu_samples = {name: [] for name in samples}
    gc_pause_samples = {name: [] for name in samples}
    liststore_signal_samples = {name: [] for name in samples}
    renderers = {
        "baseline_u0": lambda: baseline_render_library(window, entries),
        "extracted": lambda: window._render_library(entries),
    }
    reference = None
    for iteration in range(4):
        order = ("baseline_u0", "extracted") if iteration % 2 == 0 else ("extracted", "baseline_u0")
        for name in order:
            window.library_page.clear_rows()
            measured = {phase: 0.0 for phase in phases}
            calls = {phase: 0 for phase in phases}
            originals = []
            # Scoped instrumentation only: both U0 and extracted use the
            # same GObject row class, actual Gio.ListStore and GTK callbacks.
            # Never install global telemetry in the product.
            gc_pauses = []
            gc_started = {}
            store_events = []

            def on_gc(phase, info):
                generation = info.get("generation")
                if phase == "start":
                    gc_started[generation] = time.perf_counter_ns()
                elif phase == "stop":
                    start_ns = gc_started.pop(generation, None)
                    if start_ns is not None:
                        gc_pauses.append({
                            "generation": generation,
                            "duration_ms": round(
                                (time.perf_counter_ns() - start_ns) / 1_000_000, 3
                            ),
                        })

            def on_items_changed(_store, position, removed, added):
                store_events.append((position, removed, added))

            def instrument(owner, method, phase):
                original = getattr(owner, method)

                def timed(*args, **kwargs):
                    started = time.perf_counter_ns()
                    calls[phase] += 1
                    try:
                        return original(*args, **kwargs)
                    finally:
                        measured[phase] += (time.perf_counter_ns() - started) / 1_000_000

                setattr(owner, method, timed)
                originals.append((owner, method, original))

            original_row_init = BookListItem.__init__
            store_signal_id = None
            gc_watching = False

            def timed_row_init(row, *args, **kwargs):
                started_ns = time.perf_counter_ns()
                calls["row_constructor"] += 1
                try:
                    return original_row_init(row, *args, **kwargs)
                finally:
                    measured["row_constructor"] += (
                        time.perf_counter_ns() - started_ns
                    ) / 1_000_000

            try:
                BookListItem.__init__ = timed_row_init
                store_signal_id = window.library_page.store.connect(
                    "items-changed", on_items_changed,
                )
                gc.callbacks.append(on_gc)
                gc_watching = True
                instrument(window, "_attach_row_facets", "facets")
                instrument(window.library_page, "show_rows", "gtk_show_rows")
                instrument(window, "_select_pending_library_item", "pending_selection")
                instrument(window, "_library_selected", "selection_callback")
                before_gc = tuple(g["collections"] for g in gc.get_stats())
                cpu_started = time.process_time()
                started = time.perf_counter()
                renderers[name]()
                elapsed_ms = (time.perf_counter() - started) * 1000
                cpu_ms = (time.process_time() - cpu_started) * 1000
                after_gc = tuple(g["collections"] for g in gc.get_stats())
            finally:
                # Restore all per-render instrumentation, even on errors.
                if gc_watching:
                    gc.callbacks.remove(on_gc)
                if store_signal_id is not None:
                    window.library_page.store.disconnect(store_signal_id)
                BookListItem.__init__ = original_row_init
                for owner, method, original in reversed(originals):
                    setattr(owner, method, original)

            store = window.library_page.store
            rows = tuple(
                tuple(getattr(store.get_item(i), attr)
                      for attr in ("key", "title", "subtitle", "meta", "badge", "thumbnail_url"))
                for i in range(store.get_n_items())
            )
            if reference is None:
                reference = rows
            assert rows == reference, "La projection extraite diffère du rendu U0"
            assert len(rows) == 563 and window.library_page.selection.get_selected_item() is None
            assert list(window._library_objects) == [row[0] for row in reference]
            assert calls["facets"] == calls["gtk_show_rows"] == calls["pending_selection"] == 1
            assert calls["row_constructor"] == 563
            assert sum(event[2] for event in store_events) == 563
            # Callbacks may be fired by the GTK store and by the explicit
            # empty-selection fallback; report counts, not an assumption.
            if iteration:  # First pair warms both renderers; not a sample.
                samples[name].append(round(elapsed_ms, 3))
                cpu_samples[name].append(round(cpu_ms, 3))
                gc_pause_samples[name].append(gc_pauses)
                liststore_signal_samples[name].append(store_events)
                gc_collections[name].append(tuple(
                    current - previous for previous, current in zip(before_gc, after_gc)
                ))
                for phase in phases:
                    phase_samples[name][phase].append(round(measured[phase], 3))
                    phase_calls[name][phase].append(calls[phase])
    medians = {name: round(statistics.median(values), 3) for name, values in samples.items()}
    phase_medians = {
        name: {
            phase: round(statistics.median(phase_samples[name][phase]), 3)
            for phase in phases
        }
        for name in samples
    }
    return {
        "reference_blob": "6ec09453f1495ac0415ef27e0f4fa31535af282d",
        "rows": len(entries), "mode": "synchronous_full_rebuild_same_current_widgets",
        "samples_ms": samples, "median_ms": medians,
        "extracted_over_u0": round(medians["extracted"] / max(medians["baseline_u0"], 0.001), 3),
        "row_projection_equal": True,
        "phase_samples_ms": phase_samples,
        "phase_median_ms": phase_medians,
        "phase_call_counts": phase_calls,
        "process_cpu_samples_ms": cpu_samples,
        "gc_pause_events": gc_pause_samples,
        "liststore_items_changed": liststore_signal_samples,
        "gc_collection_deltas_by_generation": gc_collections,
        "phase_limits": (
            "Measured spans are inclusive; GTK show_rows includes signals; selection_callback "
            "may be nested. GObject row constructors and ListStore items-changed "
            "are observed symmetrically, but row timing is inclusive and GC callbacks "
            "perturb timing. Three pairs only; no frame, network or live provider "
            "latency; no causal conclusion from a single sampling campaign."
        ),
        "limits": "Same runtime/widgets; excludes frame latency and live providers; no absolute timing gate.",
    }


# Same frozen U0 blob as baseline_render_library; product never calls this.
def baseline_render_recent(self, items: list[dict[str, Any]] | None = None) -> None:
    if not hasattr(self, "recent_page"):
        return
    _perf_started = time.perf_counter()
    if items is None:
        items = list(self._recent_entries_cache)
    self._recent_entries_cache = list(items)
    self._recent_objects = {}
    rows: list[BookListItem] = []
    for item in items:
        key = str(item.get("placement_id") or f"recent:{len(rows)}")
        self._recent_objects[key] = item
        provider = str(item.get("provider_id") or "local")
        temporal = format_recent_timestamp(item.get("acquired_at"))
        rows.append(BookListItem(
            key,
            str(item.get("title") or "Sans titre"),
            str(item.get("author") or ""),
            temporal,
            "Kobo" if provider == "kobo" else provider.capitalize(),
            item.get("thumbnail_url"),
        ))
    self._attach_row_facets(rows,self._recent_objects,self.search_controls)
    self.recent_page.show_rows(rows)
    self.ui_perf.record("render_recent", (time.perf_counter() - _perf_started) * 1000.0)

def compare_recent_render(window):
    """Same full-rebuild protocol as Library, without a timing success gate."""
    items = list(window._recent_entries_cache)
    assert len(items) == 563
    samples = {"baseline_u0": [], "extracted": []}
    renderers = {
        "baseline_u0": lambda: baseline_render_recent(window, items),
        "extracted": lambda: window._render_recent(items),
    }
    reference = None
    for iteration in range(4):
        order = ("baseline_u0", "extracted") if iteration % 2 == 0 else ("extracted", "baseline_u0")
        for name in order:
            window.recent_page.clear_rows()
            started = time.perf_counter()
            renderers[name]()
            elapsed_ms = (time.perf_counter() - started) * 1000
            store = window.recent_page.store
            rows = tuple(
                tuple(getattr(store.get_item(i), attr)
                      for attr in ("key", "title", "subtitle", "meta", "badge", "thumbnail_url"))
                for i in range(store.get_n_items())
            )
            if reference is None:
                reference = rows
            assert rows == reference, "La projection Récents extraite diffère du rendu U0"
            assert len(rows) == 563 and window.recent_page.selection.get_selected_item() is None
            assert list(window._recent_objects) == [row[0] for row in reference]
            if iteration:
                samples[name].append(round(elapsed_ms, 3))
    medians = {name: round(statistics.median(values), 3) for name, values in samples.items()}
    return {
        "reference_blob": "6ec09453f1495ac0415ef27e0f4fa31535af282d",
        "rows": len(items), "mode": "synchronous_full_rebuild_same_current_widgets",
        "samples_ms": samples, "median_ms": medians,
        "extracted_over_u0": round(medians["extracted"] / max(medians["baseline_u0"], 0.001), 3),
        "row_projection_equal": True,
        "limits": "Same runtime/widgets; excludes frame latency and live providers; no absolute timing gate.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366, 1600), required=True)
    parser.add_argument("--slow-check", action="store_true")
    parser.add_argument("--window-baseline", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK/X11/Xvfb required")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    broker = SlowNavigationBroker() if args.slow_check else Broker()
    tasks = StateTasks() if args.slow_check else Tasks()
    page_plugin = importlib.import_module("cifereca_diagnostics_page").DiagnosticsPagePlugin()
    page_plugin.activate(Context(broker, tasks))
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    started = monotonic()
    report = {
        "status": "RUNNING",
        "scenario": f"diagnostics-navigation-slow-{args.width}" if args.slow_check
                    else f"diagnostics-navigation-{args.width}",
        "width": args.width, "git_sha": sha, "backend": "GTK4/X11/XTEST",
        "scope": "real MainWindow and real Diagnostics page contribution, simulated brokers",
        "checks": {}, "screenshots": [], "pointer_actions": [],
    }
    window = [None]
    device = [None]
    rc = [1]
    diagnostics_deadline = [None]
    slow_started = [None]
    d16_original_size = [None]
    sync_progress_check = {}

    def save():
        report["duration_seconds"] = round(monotonic() - started, 2)
        report["active_checks"] = broker.checks
        report["event_queries"] = len(broker.event_queries)
        (out / "screen.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    def capture(stage):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert image.size == (args.width, 1000 if args.width == 1600 else 900), image.size
        assert image.getbbox() is not None
        file = out / (stage + ".png")
        image.save(file)
        shutil.copyfile(file, out / "screen.png")
        selected = window[0].nav_list.get_selected_row()
        report["screenshots"].append({
            "name": stage, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "route": window[0].view_stack.get_visible_child_name(),
            "selected_navigation": selected.get_name() if selected else None,
        })

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        cleanup_constellations()
        cleanup_loan_sync()
        report["status"] = "FAIL"
        report["error"] = str(exc)
        gate = sync_progress_check.get("gate")
        if gate is not None:
            gate.set()
        if window[0] is not None:
            try:
                capture("failure")
            except Exception:
                pass
        save()
        app.quit()

    def later(delay_ms, callback):
        def call():
            try:
                callback()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(delay_ms, call)

    def await_route(route, callback, max_wait=8):
        deadline = monotonic() + max_wait
        stack = window[0].view_stack

        def check():
            # The route switches *before* the Gtk.Stack CROSSFADE ends.
            # Never use a half-transparent blend as a screenshot of UX state.
            visible = stack.get_visible_child_name() == route
            fading = bool(stack.get_property("transition-running"))
            if visible and not fading:
                # One frame of settling after the transition-running flag.
                later(120, callback)
            elif monotonic() > deadline:
                raise AssertionError(
                    f"Navigation {route!r} non stabilisée "
                    f"(visible={visible}, transition={fading})"
                )
            else:
                later(90, check)
        later(140, check)

    def labels_have(text):
        page = window[0]._plugin_page_widgets["diagnostics"]
        return any(
            isinstance(w, Gtk.Label) and text in (w.get_text() or "")
            for w in walk(page)
        )

    def initial():
        w = window[0]
        assert "library" in w.nav_rows and "diagnostics" in w.nav_rows
        assert w.view_stack.get_visible_child_name() == "dashboard"
        assert not w.nav_rows["diagnostics"].get_mapped(), "Configuration doit être repliée"
        report["checks"]["real_mainwindow_and_plugin_contribution"] = True
        capture("00-real-cifereca-shell")
        report["pointer_actions"].append(device[0].click_widget(w.nav_rows["library"]))
        await_route("library", library_open)

    def library_open():
        w = window[0]
        selected = w.nav_list.get_selected_row()
        assert selected is w.nav_rows["library"]
        assert w.root_stack.get_visible_child_name() == "main"
        report["checks"]["physical_library_navigation"] = True
        capture("01-library-selected")
        toggles = [
            widget for widget in walk(w.sidebar)
            if isinstance(widget, Gtk.ToggleButton)
            and "Configuration / Technique" in (widget.get_tooltip_text() or "")
        ]
        assert len(toggles) == 1, f"Configuration group: {len(toggles)}"
        assert not toggles[0].get_active()
        report["pointer_actions"].append(device[0].click_widget(toggles[0]))
        later(250, config_expanded)

    def config_expanded():
        w = window[0]
        assert w.nav_rows["diagnostics"].get_visible()
        assert w.nav_rows["diagnostics"].get_mapped()
        report["checks"]["physical_configuration_group_expansion"] = True
        report["pointer_actions"].append(device[0].click_widget(w.nav_rows["diagnostics"]))
        diagnostics_deadline[0] = monotonic() + 9
        await_route("diagnostics", diagnostics_open)

    def diagnostics_open():
        w = window[0]
        visible = {
            "selected": w.nav_list.get_selected_row() is w.nav_rows["diagnostics"],
            "subtitle": w.header_subtitle.get_text() == "Diagnostics",
            "mapped": w._plugin_page_widgets["diagnostics"].get_mapped(),
            "report": labels_have("Rapport support"),
            "journals": labels_have("Journaux par composant"),
            "system": labels_have("État système"),
            "page_loaded": labels_have("50 événements affichés"),
            "one_query": len(broker.event_queries) == 1,
        }
        if not all(visible.values()) and monotonic() < diagnostics_deadline[0]:
            later(160, diagnostics_open)
            return
        assert all(visible.values()), f"Diagnostics incomplet après navigation: {visible}"
        assert broker.checks == 0, "Diagnostic actif déclenché à la navigation"
        report["checks"]["real_diagnostics_route_and_visible_content"] = True
        report["checks"]["no_implicit_active_diagnostics"] = True
        capture("02-real-diagnostics-route")
        if args.slow_check:
            later(130, initiate_slow_check)
            return
        report["pointer_actions"].append(device[0].click_widget(w.nav_rows["library"]))
        await_route("library", returned_to_library)

    def initiate_slow_check():
        page = window[0]._plugin_page_widgets["diagnostics"]
        buttons = [
            w for w in walk(page) if isinstance(w, Gtk.Button)
            and w.get_tooltip_text() == "Vérifier les services configurés et runtimes"
        ]
        assert len(buttons) == 1 and buttons[0].get_mapped()
        assert broker.checks == 0, "Contrôle implicite avant geste utilisateur"
        slow_started[0] = monotonic()
        report["pointer_actions"].append(device[0].click_widget(buttons[0]))
        later(100, slow_check_pending)

    def slow_check_pending():
        handle = page_plugin._check_handle
        assert handle is not None and not handle.done()
        assert broker.checks == 1
        assert labels_have("Vérification en cours")
        report["checks"]["D04_explicit_async_check_started"] = True
        report["pointer_actions"].append(
            device[0].click_widget(window[0].nav_rows["library"])
        )
        await_route("library", library_while_check_pending)

    def library_while_check_pending():
        w = window[0]
        assert w.view_stack.get_visible_child_name() == "library"
        assert w.nav_list.get_selected_row() is w.nav_rows["library"]
        assert monotonic() - slow_started[0] < 1.15
        assert not page_plugin._check_handle.done(), "Navigation a attendu le contrôle"
        report["checks"]["D04_physical_library_navigation_during_pending_check"] = True
        capture("03-library-while-diagnostics-check-pending")
        deadline = monotonic() + 5

        def wait_completed():
            if labels_have("Contrôle local fictif terminé"):
                assert broker.checks == 1
                assert page_plugin._check_handle is None
                finish_slow_navigation()
            elif monotonic() >= deadline:
                raise AssertionError("Contrôle asynchrone non terminé après navigation")
            else:
                later(90, wait_completed)
        later(100, wait_completed)

    def finish_slow_navigation():
        report["checks"]["D04_async_check_completes_offscreen"] = True
        capture("04-library-after-diagnostics-check")
        report["pointer_actions"].append(
            device[0].click_widget(window[0].nav_rows["diagnostics"])
        )
        await_route("diagnostics", slow_diagnostics_return)

    def slow_diagnostics_return():
        assert labels_have("Contrôle local fictif terminé")
        assert window[0].nav_list.get_selected_row() is window[0].nav_rows["diagnostics"]
        report["checks"]["D04_check_result_visible_on_return"] = True
        capture("05-diagnostics-result-after-navigation")
        if args.window_baseline:
            # D16: unlike the isolated widget tests, this measures the real
            # MainWindow + sidebar + actual Diagnostics contribution on both
            # 1366 and 1600 X11 displays, after a completed active check.
            later(150, begin_d16_mainwindow)
            return
        return_to_library_after_diagnostics()

    def return_to_library_after_diagnostics():
        report["pointer_actions"].append(
            device[0].click_widget(window[0].nav_rows["library"])
        )
        await_route("library", returned_to_library)

    def d16_geometry():
        w = window[0]
        widget = w._plugin_page_widgets["diagnostics"]
        assert widget.get_mapped(), "D16: Diagnostics absent de MainWindow"
        scrollers = [node for node in walk(widget)
                     if isinstance(node, Gtk.ScrolledWindow)]
        assert scrollers, "D16: viewport Diagnostics introuvable"
        outer = scrollers[0]
        adjustment = outer.get_hadjustment()
        overflow = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        assert adjustment.get_page_size() > 100, "D16: viewport sans allocation GTK"
        assert overflow <= 5, f"D16: débordement horizontal global {overflow:.1f} px"
        actions = (
            "Actualiser les diagnostics",
            "Vérifier les services configurés et runtimes",
        )
        geometry = []
        for tooltip in actions:
            found = [node for node in walk(widget)
                     if isinstance(node, Gtk.Button)
                     and node.get_tooltip_text() == tooltip]
            assert len(found) == 1, f"D16: bouton absent ou doublé: {tooltip}"
            button = found[0]
            assert button.get_mapped() and button.get_sensitive(), (
                "D16: bouton essentiel inactif ou non rendu", tooltip
            )
            ok, bounds = button.compute_bounds(outer.get_child())
            assert ok and bounds.get_width() > 0 and bounds.get_height() > 0, (
                "D16: contrôle essentiel sans géométrie", tooltip
            )
            geometry.append({
                "tooltip": tooltip,
                "x": round(bounds.get_x(), 2),
                "width": round(bounds.get_width(), 2),
            })
        assert labels_have("Rapport support") and labels_have("Analyse IA")
        assert w.header_subtitle.get_text() == "Diagnostics"
        assert w.nav_list.get_selected_row() is w.nav_rows["diagnostics"]
        return {
            "window_size": [w.get_width(), w.get_height()],
            "diagnostics_horizontal_overflow": round(overflow, 2),
            "essential_actions": geometry,
        }

    def begin_d16_mainwindow():
        report["stage"] = "D16_full_mainwindow_native"
        report["D16_native"] = d16_geometry()
        d16_original_size[0] = (window[0].get_width(), window[0].get_height())
        report["checks"]["D16_real_mainwindow_native_1366_or_1600"] = True
        capture("08-d16-diagnostics-native-mainwindow")
        # Resize the *GTK window*, not the Xvfb display. No services are called.
        window[0].set_default_size(1240, 760 if args.width == 1366 else 800)
        deadline = monotonic() + 7

        def wait_narrow():
            size = (window[0].get_width(), window[0].get_height())
            # MainWindow may enforce a legitimate GTK minimum height
            # (observed 808 px for a requested 760 px). D16 tests the
            # actual reduced *width*, and reports the true height.
            if size[0] <= 1260:
                assert size[1] <= (900 if args.width == 1366 else 1000), (
                    "D16: fenêtre plus haute que l'écran Xvfb", size
                )
                d16_narrow()
            elif monotonic() >= deadline:
                raise AssertionError(f"D16: largeur étroite non appliquée: {size}")
            else:
                later(95, wait_narrow)
        later(180, wait_narrow)

    def d16_narrow():
        report["stage"] = "D16_full_mainwindow_narrow"
        report["D16_narrow"] = d16_geometry()
        assert window[0].get_width() < d16_original_size[0][0] - 50
        assert broker.checks == 1 and len(broker.event_queries) == 1
        assert labels_have("Contrôle local fictif terminé")
        report["checks"]["D16_real_mainwindow_narrow_no_horizontal_overflow"] = True
        buttons = [node for node in walk(window[0]._plugin_page_widgets["diagnostics"])
                   if isinstance(node, Gtk.Button)
                   and node.get_tooltip_text() == "Vérifier les services configurés et runtimes"]
        assert len(buttons) == 1 and buttons[0].grab_focus() is not False
        deadline = monotonic() + 4

        def verify_focus():
            if buttons[0].has_focus():
                report["checks"]["D16_real_mainwindow_narrow_action_keyboard_focus"] = True
                capture("09-d16-diagnostics-narrow-mainwindow")
                window[0].set_default_size(*d16_original_size[0])
                later(180, d16_wait_restored)
            elif monotonic() >= deadline:
                raise AssertionError("D16: focus clavier sur Vérifier indisponible")
            else:
                later(90, verify_focus)
        later(160, verify_focus)

    def d16_wait_restored():
        native = d16_original_size[0]
        size = (window[0].get_width(), window[0].get_height())
        if size[0] >= native[0] - 20 and size[1] >= native[1] - 20:
            report["stage"] = "D16_full_mainwindow_restored"
            report["D16_restored"] = d16_geometry()
            assert broker.checks == 1 and len(broker.event_queries) == 1
            assert labels_have("Contrôle local fictif terminé")
            report["checks"]["D16_real_mainwindow_restored_preserves_state"] = True
            capture("10-d16-diagnostics-restored-mainwindow")
            return_to_library_after_diagnostics()
        else:
            report["D16_resize_poll"] = report.get("D16_resize_poll", 0) + 1
            assert report["D16_resize_poll"] <= 70, (
                "D16: restauration fenêtre non appliquée", size, native
            )
            later(100, d16_wait_restored)

    def returned_to_library():
        w = window[0]
        assert w.nav_list.get_selected_row() is w.nav_rows["library"]
        assert broker.checks == (1 if args.slow_check else 0)
        report["checks"]["physical_return_to_library"] = True
        capture("03-return-library")
        if args.window_baseline:
            begin_baseline()
            return
        finish()

    def begin_baseline():
        w = window[0]
        assert w.service.current_identity.identity_id == "camille"
        assert w.identity_dropdown.get_model().get_n_items() == 2
        report["window_size"] = [w.get_width(), w.get_height()]
        begin_library_volume()

    library_volume = {}

    def begin_library_volume():
        # Acceptance #1129: use the real projection, Gio.ListStore, filtering,
        # selection signals and DetailPanel. These are fictional loaded notices,
        # not a provider fixture or evidence of a successful remote sync.
        from dataclasses import replace
        w = window[0]
        library_volume["entries"] = list(w._library_entries_cache)
        library_volume["state"] = w.library_controls.state()
        state = replace(w.library_controls.state(), text="", field="all",
                        scope="all", providers=(), types=(), languages=())
        w.library_controls.set_state(state, notify=False)
        entries = [dict(
            key=f"p0-volume-{index:03d}", title=f"Notice volume {index:03d}",
            author="Auteur fictif", identity_id="camille", provider_id="local",
            medium="digital", media_format="epub", thumbnail_url=None,
            metadata={"language": "fr"},
        ) for index in range(563)]
        w._render_library(entries)
        later(150, library_volume_rendered)

    def library_volume_rendered():
        from dataclasses import replace
        w = window[0]
        page = w.library_page
        assert page.get_mapped() and page.listview.get_mapped()
        assert page.listview.get_width() > 0
        assert page.store.get_n_items() == 563
        assert len(w._library_objects) == 563
        assert "563" in page.count_label.get_text()
        assert page.selection.get_selected_item() is None
        assert all(page.store.get_item(i).thumbnail_url is None for i in range(563))
        report["checks"]["P0_library_563_real_rows_no_autoselection"] = True
        report["library_563_render_perf"] = w.ui_perf.snapshot()["render_library"]
        capture("03b-library-563-real-gtk")
        report["library_563_u0_comparison"] = compare_library_render(w)
        report["checks"]["P0_library_563_u0_projection_equivalent"] = True
        state = replace(w.library_controls.state(), text="Notice volume 562", field="title")
        library_volume["filtered"] = state
        w.library_controls.set_state(state, notify=False)
        w._render_library()
        assert page.store.get_n_items() == 1
        assert page.store.get_item(0).key == "p0-volume-562"
        assert "563" in page.count_label.get_text()
        assert page.selection.get_selected_item() is None
        w._open_library_item("p0-volume-562")
        later(150, library_volume_selected)

    def library_volume_selected():
        w = window[0]
        assert w.library_controls.state() == library_volume["filtered"]
        assert w.library_page.selection.get_selected_item().key == "p0-volume-562"
        assert any(
            isinstance(node, Gtk.Label) and node.get_text() == "Notice volume 562"
            for node in walk(w.library_page.detail)
        ), "Titre absent du vrai panneau de détail Bibliothèque"
        assert w.library_page.detail.get_mapped()
        assert w._pending_library_selection_key is None
        report["checks"]["P0_library_563_filtered_selection_detail"] = True
        capture("03c-library-filtered-detail")
        # A book hidden by filters must not clear them or select another book.
        w._open_library_item("p0-volume-000")
        assert w.library_controls.state() == library_volume["filtered"]
        assert w.library_page.store.get_n_items() == 1
        assert w.library_page.selection.get_selected_item().key == "p0-volume-562"
        assert w._pending_library_selection_key is None
        report["checks"]["P0_library_hidden_book_keeps_real_gtk_filter_and_selection"] = True
        begin_constellations_acceptance()

    constellation_fixture = {}

    def cleanup_constellations():
        plugin = constellation_fixture.pop("plugin", None)
        if plugin is not None:
            plugin.deactivate()
        executor = constellation_fixture.pop("executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def constellation_check(name):
        origin = constellation_fixture.get("origin_route", "library")
        if origin == "search":
            name = "search_" + name.replace("from_library", "from_search")
        report["checks"]["P0_constellations_" + name] = True

    def begin_constellations_acceptance(origin_route="library"):
        # Real plugin factory + Core public host/registry; only its metadata,
        # identities and task scheduling brokers are isolated here.
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from types import SimpleNamespace as NS
        sys.path.insert(0, str(ROOT / "src/plugins/constellations"))
        from constellations_page import ConstellationsPlugin
        w = window[0]
        assert w.view_stack.get_visible_child_name() == origin_route
        constellation_fixture["origin_route"] = origin_route
        constellation_fixture["listener_count"] = len(w._overview_listeners)
        history_before = w._navigation_history.current
        filters_before = (w.library_controls.state(), w.search_controls.state())
        w.navigate("p0-unknown-route-must-be-inert")
        assert w.view_stack.get_visible_child_name() == origin_route
        assert w._navigation_history.current == history_before
        assert (w.library_controls.state(), w.search_controls.state()) == filters_before
        assert w.service.current_identity.identity_id == "camille"
        constellation_check("unknown_route_preserves_real_window_context")
        rows = tuple(w._library_entries_cache[i] for i in (562, 0, 1))
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="p0-constellations")
        constellation_fixture["executor"] = executor
        requests = []
        def library_entries(*, identity_id, scan):
            assert identity_id == "camille" and scan is False
            requests.append((identity_id, scan))
            return rows
        def submit(operation, **options):
            cancelled = Event()
            control = NS(cancelled=cancelled.is_set)
            future = executor.submit(operation, control)
            handle = NS(control=control, cancel=cancelled.set, result=future.result)
            handle.add_done_callback = lambda callback: future.add_done_callback(lambda _: callback(handle))
            return handle
        def forbidden_search(*args, **kwargs):
            raise AssertionError("Constellations a lancé une recherche distante implicite")
        profiles = [{"identity_id": "camille", "label": "Camille"}]
        constellation_fixture["profiles"] = profiles
        brokers = {
            "metadata": NS(library_entries=library_entries),
            "identities": NS(identities=lambda: list(profiles)),
            "tasks": NS(submit=submit),
            "catalog": NS(search=forbidden_search),
        }
        plugin = ConstellationsPlugin().activate(NS(config={}, broker=brokers.__getitem__))
        constellation_fixture.update(plugin=plugin, requests=requests)
        page = plugin.pages()[0]
        assert page.route not in w._pages
        # Register the actual public contribution in the existing isolated
        # MainWindow stack. Package installation/reload is outside this test.
        widget = page.factory(w._UiHost(w))
        w._pages[page.route] = page
        w._plugin_page_widgets[page.route] = widget
        w.view_stack.add_named(widget, page.route)
        source_page = w.search_page if origin_route == "search" else w.library_page
        if origin_route == "search":
            w._search_selected(source_page.selection.get_selected_item())
        else:
            w._library_selected(source_page.selection.get_selected_item())
        buttons = [
            node for node in walk(source_page.detail)
            if isinstance(node, Gtk.Button)
            and getattr(node, "_cifereca_action_id", "") == "plugin:constellations.explore"
        ]
        assert len(buttons) == 1, f"Action Constellations absente ou dupliquée dans {origin_route}"
        constellation_fixture["origin_action"] = buttons[0]
        assert plugin.pending is None and plugin.view.center is None
        assert not requests, "Collecte Constellations avant activation utilisateur"
        constellation_check("real_context_action_no_implicit_load")
        # Native GTK activation signals exercise the registered Core callback;
        # these steps are not represented as XTEST pointer gestures.
        buttons[0].emit("clicked")
        await_route("constellations", lambda: wait_constellation_center("Notice volume 562", constellation_a))

    def wait_constellation_center(title, callback):
        deadline = monotonic() + 6
        def check():
            view = constellation_fixture["plugin"].view
            if view.center is not None and view.center.book.title == title:
                callback()
            elif monotonic() >= deadline:
                raise AssertionError(f"Centre Constellations non obtenu : {title}")
            else:
                later(80, check)
        later(100, check)

    def constellation_a():
        view = constellation_fixture["plugin"].view
        assert view.origin_identity == "camille"
        assert view.origin_book.title == "Notice volume 562"
        assert view.widget.get_mapped() and view.master.widget.get_mapped()
        assert constellation_fixture["requests"], "Snapshot local jamais lu"
        constellation_check("origin_book_and_profile_from_library")
        visit_constellation_neighbour("Notice volume 000", constellation_b)

    def visit_constellation_neighbour(title, callback):
        view = constellation_fixture["plugin"].view
        buttons = [
            node for node in walk(view.flow)
            if isinstance(node, Gtk.Button) and node.get_tooltip_text() == "Visiter : " + title
        ]
        assert len(buttons) == 1, f"Voisin absent ou ambigu : {title}"
        buttons[0].emit("clicked")
        wait_constellation_center(title, callback)

    def constellation_b():
        constellation_check("native_A_to_B")
        visit_constellation_neighbour("Notice volume 001", constellation_c)

    def constellation_c():
        view = constellation_fixture["plugin"].view
        assert view.origin_identity == "camille"
        assert view.origin_book.title == "Notice volume 562"
        assert view.back_button.get_sensitive()
        constellation_check("native_B_to_C_keeps_origin")
        suffix = "-search" if constellation_fixture["origin_route"] == "search" else ""
        capture("03d-constellations-center-C" + suffix)
        view.back_button.emit("clicked")
        wait_constellation_center("Notice volume 000", constellation_back)

    def constellation_back():
        constellation_check("native_history_returns_to_B")
        constellation_fixture["plugin"].view.origin_button.emit("clicked")
        await_route("library", constellation_returned)

    def constellation_returned():
        w = window[0]
        assert w.service.current_identity.identity_id == "camille"
        assert w.library_controls.state() == library_volume["filtered"]
        selected = w.library_page.selection.get_selected_item()
        assert selected is not None and selected.key == "p0-volume-562"
        assert any(
            isinstance(node, Gtk.Label) and node.get_text() == "Notice volume 562"
            for node in walk(w.library_page.detail)
        ), "Retour origine sans détail du livre A"
        constellation_check("origin_return_preserves_book_profile_filter")
        origin = constellation_fixture["origin_route"]
        if origin == "search":
            assert w.search_controls.state() == library_volume["search_expected"]
        capture("03e-constellations-return-original-library-book" + ("-search" if origin == "search" else ""))
        if origin == "search":
            reads = len(constellation_fixture["requests"])
            constellation_fixture["profiles"].clear()
            view = constellation_fixture["plugin"].view
            view.invalidate()
            assert view.identity_id() == "" and view.center is None
            assert "Aucun profil" in view.status.get_text()
            assert len(constellation_fixture["requests"]) == reads
            constellation_check("missing_profile_clears_detail_without_metadata_read")
        widget = w._plugin_page_widgets.pop("constellations")
        w.view_stack.remove(widget)
        w._pages.pop("constellations")
        cleanup_constellations()
        assert len(w._overview_listeners) == constellation_fixture["listener_count"]
        # The old real Gtk.Button must not resurrect an unregistered plugin.
        constellation_fixture["origin_action"].emit("clicked")
        assert w.view_stack.get_visible_child_name() == "library"
        assert "constellations" not in w._pages
        constellation_check("removed_action_inert_and_subscription_released")
        if origin == "library":
            begin_search_origin()
        else:
            restore_search_snapshot()
            restore_volume_snapshot()

    def begin_search_origin():
        from dataclasses import replace
        w = window[0]
        library_volume["search_snapshot"] = {
            "state": w.search_controls.state(),
            "local": dict(w._search_local_objects),
            "remote": list(w._remote_search_results),
            "objects": dict(w.search_objects),
            "mode": w._search_mode,
            "selected": w._selected_search_id,
            "global_sensitive": w.search_global_button.get_sensitive(),
        }
        state = replace(w.search_controls.state(), text="Notice volume 562", field="title",
                        scope="all", providers=(), types=(), languages=())
        w.search_controls.set_state(state, notify=False)
        library_volume["search_expected"] = w.search_controls.state()
        w._populate_local_search([w._library_entries_cache[562]])
        assert w.search_page.store.get_n_items() == 1
        assert w.search_page.select_key("p0-volume-562")
        w.navigate("search")
        await_route("search", lambda: begin_constellations_acceptance("search"))

    def restore_search_snapshot():
        w = window[0]
        snapshot = library_volume["search_snapshot"]
        w.search_controls.set_state(snapshot["state"], notify=False)
        w._search_local_objects = snapshot["local"]
        w._remote_search_results = snapshot["remote"]
        w.search_objects = snapshot["objects"]
        w._search_mode = snapshot["mode"]
        w._render_search_results()
        w._selected_search_id = snapshot["selected"]
        w.search_global_button.set_sensitive(snapshot["global_sensitive"])

    def restore_volume_snapshot():
        w = window[0]
        w.library_controls.set_state(library_volume["state"], notify=False)
        w._render_library(library_volume["entries"])
        later(100, begin_recent_volume)

    recent_volume = {}

    def begin_recent_volume():
        w = window[0]
        recent_volume["items"] = list(w._recent_entries_cache)
        selected = w.recent_page.selection.get_selected_item()
        recent_volume["selected"] = selected.key if selected is not None else None
        recent_volume["search_state"] = w.search_controls.state()
        # Acquisitions fictives déjà chargées, sans chemin ni couverture :
        # aucune ouverture de fichier ou action d'acquisition dans ce test.
        items = [dict(
            placement_id=f"p0-recent-{index:03d}", title=f"Acquisition fictive {index:03d}",
            author="Auteur fictif", provider_id="kobo" if index % 2 else "local",
            acquired_at=1791633600, thumbnail_url=None,
            metadata={"language": "fr"},
        ) for index in range(563)]
        w.recent_page.clear_rows()
        w._render_recent(items)
        w.navigate("recent")
        await_route("recent", recent_volume_rendered)

    def recent_volume_rendered():
        w = window[0]
        page = w.recent_page
        assert page.get_mapped() and page.listview.get_mapped()
        assert page.store.get_n_items() == 563 and len(w._recent_objects) == 563
        assert page.selection.get_selected_item() is None
        assert all(page.store.get_item(i).thumbnail_url is None for i in range(563))
        report["checks"]["P0_recent_563_real_rows_no_cover_or_autoselection"] = True
        report["recent_563_u0_comparison"] = compare_recent_render(w)
        report["checks"]["P0_recent_563_u0_projection_equivalent"] = True
        assert page.select_key("p0-recent-562")
        later(100, recent_volume_selected)

    def recent_volume_selected():
        w = window[0]
        assert w.recent_page.selection.get_selected_item().key == "p0-recent-562"
        assert any(isinstance(node, Gtk.Label) and node.get_text() == "Acquisition fictive 562"
                   for node in walk(w.recent_page.detail))
        assert w.recent_page.detail.get_mapped()
        assert w.search_controls.state() == recent_volume["search_state"]
        assert w.service.current_identity.identity_id == "camille"
        assert all(w.recent_page.store.get_item(i).meta != "Date inconnue" for i in range(563))
        report["checks"]["P0_recent_real_detail_preserves_identity_and_search_filter"] = True
        # The benchmark blocks the main loop; allow layout/repaint to catch up
        # before capturing and restoring the snapshot.
        later(250, recent_volume_capture)

    def recent_volume_capture():
        w = window[0]
        title = next(node for node in walk(w.recent_page.detail)
                     if isinstance(node, Gtk.Label) and node.get_text() == "Acquisition fictive 562")
        assert title.get_mapped() and title.get_width() > 0
        capture("03f-recent-563-selected-detail")
        w._render_recent(recent_volume["items"])
        if recent_volume["selected"] is not None:
            assert w.recent_page.select_key(recent_volume["selected"])
        later(100, begin_loan_sync_acceptance)

    loan_sync_fixture = {}

    def begin_loan_sync_acceptance():
        from types import SimpleNamespace as NS
        from cifereca.models import Loan
        from cifereca.core.loan_source_identity import loan_source_key
        from cifereca.plugin_api import SyncSourceRef
        w = window[0]
        f = loan_sync_fixture
        f["manager"] = w.plugin_manager
        f["loans"] = list(w.service.last_loans)
        selected = w.loan_page.selection.get_selected_item()
        f["selected"] = selected.key if selected else None
        f["displayed"] = getattr(w, "_displayed_loan", None)
        f["search_state"] = w.search_controls.state()
        f["library_state"] = w.library_controls.state()
        f["mutations"] = []
        f["overrides"] = []
        def override(owner, name, value):
            f["overrides"].append((owner, name, name in vars(owner), vars(owner).get(name)))
            setattr(owner, name, value)
        def forbidden(*args, **kwargs):
            f["mutations"].append("unexpected operation")
            raise AssertionError("Opération de restitution/synchronisation implicite")
        override(w, "_return_loan", forbidden)
        override(w, "_return_loan_pnb", forbidden)
        # Only read-side capabilities are isolated; real renderers, window
        # facades, row keys, actions and confirmation dialog remain in use.
        override(w.service, "can_download_loan", lambda loan: False)
        override(w.service, "adept_return_status_for_loan", lambda loan: {"status": "no_acquisition"})
        override(w.service, "local_placement_for_source", lambda **kw: None)
        sources = [
            # Read-only local SHA audit remains available for a disconnected,
            # disabled Kobo account; no remote provider is installed here.
            SyncSourceRef("p0-kobo-audit", "camille", "kobo", "fictitious-credential",
                          "Kobo audit hors ligne", enabled=False,
                          connected=False, status="failed"),
            SyncSourceRef("p0-partial", "camille", "paris", "fictif-a", "Source partielle fictive",
                          status="partial", last_attempt="2026-10-11T12:00:00+00:00",
                          last_success="2026-10-10T12:00:00+00:00"),
            SyncSourceRef("p0-offline", "camille", "paris", "fictif-b", "Source déconnectée fictive",
                          connected=False, status="failed"),
            SyncSourceRef("p0-public-g", "camille", "gutenberg", "", "Catalogue public",
                          sync_capable=False, enabled=False),
            SyncSourceRef("p0-public-b", "camille", "gallica", "", "Catalogue public",
                          sync_capable=False),
            SyncSourceRef("p0-paris-one", "camille", "paris", "fictif-c", "Paris homonyme",
                          status="success", last_success="2026-10-10T12:00:00+00:00",
                          sync_capabilities=("entitlements",)),
            SyncSourceRef("p0-paris-two", "camille", "paris", "fictif-d", "Paris homonyme",
                          status="error", error="Bearer PRIVATE_SYNC_ERROR",
                          sync_capabilities=("entitlements",)),
        ]
        f["source_sync_calls"] = []
        f["library_refresh_calls"] = []
        override(w, "refresh_library", lambda *args: f["library_refresh_calls"].append(args))
        def synchronize_one(source_id):
            from dataclasses import replace
            assert source_id == "p0-paris-two", "Le clic a ciblé un autre compte"
            f["source_sync_calls"].append(source_id)
            sources[-1] = replace(sources[-1], status="success", error="",
                                  last_success="2026-10-15T12:00:00+00:00")
            return NS(ok=True, changed=0)
        f["source_queries"] = []
        def read_sources(identity_id):
            assert identity_id == "camille"
            f["source_queries"].append(identity_id)
            return sources
        f["audit_calls"] = []

        def audit_local_reconciliation(source_id):
            assert source_id == "p0-kobo-audit"
            f["audit_calls"].append(source_id)
            return NS(
                ok=True, changed=0,
                facts={
                    "phase": "reconcile", "status": "audit_only",
                    "read_only": True, "offline": True,
                    "acquisition_authorized": False, "truncated": True,
                    "counts": {
                        "backup_rows": 4,
                        "linked_files_verified": 3,
                        "unverified_placements": 1,
                        "missing_or_unlinked": 0,
                        "stored_hash_mismatch": 1,
                        "unproven_catalogue_links": 0,
                        "same_product_different_bytes": 0,
                        "binary_match_groups": 1,
                        "active_entitlements_unmaterialized": 2,
                        "unindexed_regular_file_candidates": 1,
                        "identity_flat_file_candidates": 0,
                    },
                },
            )

        sync_broker = NS(
            sources=read_sources, synchronize=forbidden,
            synchronize_source=synchronize_one, set_enabled=forbidden,
            audit_local_reconciliation=audit_local_reconciliation,
        )
        class FixtureManager:
            active = True
            def broker(self, name):
                if self.active and name == "sync":
                    return sync_broker
                original = getattr(f["manager"], "broker", None)
                return original(name) if callable(original) else None
            def __getattr__(self, name):
                return getattr(f["manager"], name)
        f["proxy"] = FixtureManager()
        w.plugin_manager = f["proxy"]
        loans = [Loan(
            id="p0-same-upstream-loan", provider="SYRACUSE", medium="digital",
            record_id=f"p0-record-{index}", title=f"Prêt fictif compte {index}",
            source_provider="paris", source_credential=f"p0-credential-{index}",
            source_account_id=f"p0-account-{index}", identity_id="camille",
            author="Auteur fictif", media_format="epub", can_return=True,
            due_at="2026-11-10", raw={"language": "fr"},
        ) for index in (1, 2)]
        f["rows"] = loans
        f["keys"] = [loan_source_key(loan) for loan in loans]
        assert f["keys"][0] != f["keys"][1]
        assert w.loan_filter == "digital" and not w.loan_languages.values()
        w.loan_page.clear_rows()
        w._populate_loans(loans)
        w.navigate("loans")
        await_route("loans", loan_sync_rows_rendered)

    def loan_sync_rows_rendered():
        w = window[0]
        f = loan_sync_fixture
        assert w.loan_page.store.get_n_items() == 2
        assert set(w.loan_objects) == set(f["keys"])
        assert w.loan_page.selection.get_selected_item() is None
        assert w.loan_page.select_key(f["keys"][1])
        later(200, loan_sync_detail_rendered)

    def loan_sync_detail_rendered():
        w = window[0]
        f = loan_sync_fixture
        title = next(node for node in walk(w.loan_page.detail)
                     if isinstance(node, Gtk.Label) and node.get_text() == "Prêt fictif compte 2")
        assert title.get_mapped() and title.get_width() > 0
        assert w._displayed_loan is f["rows"][1]
        assert w.loan_page.selection.get_selected_item().key == f["keys"][1]
        assert not f["mutations"] and w.service.current_identity.identity_id == "camille"
        report["checks"]["P0_loans_real_gtk_source_qualified_rows_and_detail"] = True
        capture("03g-loans-two-source-rows-selected-detail")
        w.confirm_return(f["rows"][1], Gtk.Button())
        later(250, loan_sync_return_dialog)

    def loan_sync_return_dialog():
        w = window[0]
        dialogs = [node for node in Gtk.Window.list_toplevels()
                   if isinstance(node, Adw.MessageDialog) and node.get_visible()
                   and node.get_heading() == "Restituer ce prêt numérique ?"]
        assert len(dialogs) == 1 and dialogs[0].get_mapped()
        dialog = dialogs[0]
        assert dialog.has_response("adept") and dialog.has_response("pnb")
        assert not loan_sync_fixture["mutations"]
        report["checks"]["P0_loan_return_real_gtk_separates_ADEPT_and_PNB"] = True
        capture("03h-loan-return-confirmation")
        cancel = next(node for node in walk(dialog)
                      if isinstance(node, Gtk.Button)
                      and str(node.get_label()).replace("_", "") == "Annuler")
        loan_sync_fixture["dialog"] = dialog
        report["pointer_actions"].append(device[0].click_widget(cancel))
        later(250, loan_sync_return_cancelled)

    def loan_sync_return_cancelled():
        w = window[0]
        f = loan_sync_fixture
        assert not f["dialog"].get_visible()
        assert not f["mutations"] and w.service.last_loans == f["rows"]
        assert w.loan_page.selection.get_selected_item().key == f["keys"][1]
        report["checks"]["P0_loan_return_XTEST_cancel_keeps_loan_without_mutation"] = True
        w.navigate("sync")
        await_route("sync", loan_sync_sources_rendered)

    def loan_sync_sources_rendered():
        w = window[0]
        f = loan_sync_fixture
        rows = [node for node in walk(w._sync_sources_box) if isinstance(node, Adw.ActionRow)]
        partial = next(row for row in rows if row.get_title() == "Source partielle fictive")
        offline = next(row for row in rows if row.get_title() == "Source déconnectée fictive")
        assert partial.get_mapped() and partial.get_width() > 0
        assert "Synchronisation : Partielle" in partial.get_subtitle()
        assert "Dernier succès confirmé : 10/10/2026 12:00" in partial.get_subtitle()
        assert "Connexion : Déconnectée" in offline.get_subtitle()
        assert any(isinstance(node, Gtk.Button) and not node.get_sensitive()
                   and node.get_tooltip_text() == "Reconnectez ce compte avant de synchroniser."
                   for node in walk(offline))
        assert w._sync_all_button.get_sensitive()
        assert f["source_queries"] and not f["mutations"]
        assert w.service.current_identity.identity_id == "camille"
        assert w.search_controls.state() == f["search_state"]
        assert w.library_controls.state() == f["library_state"]
        report["checks"]["P0_sync_real_gtk_partial_preserves_last_success_and_identity"] = True
        report["checks"]["P0_sync_real_gtk_disconnected_source_action_disabled"] = True
        capture("03i-sync-partial-last-success-and-offline-source")
        # #980 acceptance uses the real MainWindow/controller/widgets and
        # XTEST, with seven synthetic broker rows; no provider/network/DRM.
        assert len(rows) == 9  # Seven sources plus Kobo preview/audit outputs.
        for provider in ("gutenberg", "gallica"):
            row = next(row for row in rows if row.get_title() == f"Catalogue public · {provider}")
            assert f"Fournisseur : {provider}" in row.get_subtitle()
            assert "Non prise en charge" in row.get_subtitle()
            assert "Catalogue public consultable indépendamment" in row.get_subtitle()
            assert "Échec" not in row.get_subtitle()
            assert any(isinstance(node, Gtk.Button) and not node.get_sensitive()
                       and "ne fournit pas de synchronisation" in (node.get_tooltip_text() or "")
                       for node in walk(row))
        paris = [next(row for row in rows
                      if row.get_title() == f"Paris homonyme · paris · connexion {i}/2")
                 for i in (1, 2)]
        assert "Dernier succès 10/10/2026 12:00" in paris[0].get_subtitle()
        assert "Synchronisation : Échec" in paris[1].get_subtitle()
        for row in paris:
            assert "Portée de cette source : droits et emprunts." in row.get_subtitle()
            assert "ne lance pas la synchronisation globale au démarrage" in row.get_subtitle()
        assert not f["source_sync_calls"] and not f["library_refresh_calls"]
        visible_text = "\n".join(row.get_title() + row.get_subtitle() for row in rows)
        assert "PRIVATE_SYNC_ERROR" not in visible_text and "fictif-" not in visible_text
        report["checks"]["P0_980_seven_sources_homonyms_states_scope_no_implicit_sync"] = True
        # Bring both homonymous Paris rows into the viewport before a physical
        # click. Mapping alone does not prove the action is visible on screen.
        scroll = w._sync_sources_box.get_parent().get_parent()
        while not isinstance(scroll, Gtk.ScrolledWindow):
            scroll = scroll.get_parent()
        f["sync_scroll"] = scroll
        adjustment = scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        later(200, sync_homonyms_click)

    def sync_homonyms_click():
        w = window[0]
        f = loan_sync_fixture
        row = next(row for row in walk(w._sync_sources_box)
                   if isinstance(row, Adw.ActionRow)
                   and row.get_title() == "Paris homonyme · paris · connexion 2/2")
        button = next(node for node in walk(row) if isinstance(node, Gtk.Button)
                      and node.get_tooltip_text() == "Synchroniser Paris homonyme")
        assert button.get_sensitive() and button.get_mapped()
        ok, bounds = button.compute_bounds(f["sync_scroll"])
        assert ok and bounds.get_y() >= 0
        assert bounds.get_y() + bounds.get_height() <= f["sync_scroll"].get_height()
        capture("03i2-sync-public-and-paris-homonyms")
        report["pointer_actions"].append(device[0].click_widget(button))
        f["source_sync_deadline"] = monotonic() + 6
        later(100, sync_homonyms_completed)

    def sync_homonyms_completed():
        w = window[0]
        f = loan_sync_fixture
        rows = [node for node in walk(w._sync_sources_box) if isinstance(node, Adw.ActionRow)]
        row = next(row for row in rows if row.get_title() == "Paris homonyme · paris · connexion 2/2")
        if "Dernier succès 15/10/2026 12:00" not in row.get_subtitle():
            assert monotonic() < f["source_sync_deadline"], "Résultat de synchronisation absent"
            later(100, sync_homonyms_completed)
            return
        assert f["source_sync_calls"] == ["p0-paris-two"]
        assert f["library_refresh_calls"] == [(False,)]
        assert not f["mutations"] and w.service.current_identity.identity_id == "camille"
        assert w.search_controls.state() == f["search_state"]
        assert w.library_controls.state() == f["library_state"]
        report["checks"]["P0_980_XTEST_second_homonym_only_and_success_feedback"] = True
        # A changed GObject subtitle is observable before the next paint.
        # Capture the successful feedback only after GTK has repainted it.
        later(200, sync_homonyms_capture_success)

    def sync_homonyms_capture_success():
        w = window[0]
        f = loan_sync_fixture
        row = next(row for row in walk(w._sync_sources_box)
                   if isinstance(row, Adw.ActionRow)
                   and row.get_title() == "Paris homonyme · paris · connexion 2/2")
        assert "Dernier succès 15/10/2026 12:00" in row.get_subtitle()
        capture("03i3-sync-targeted-success")
        f["sync_scroll"].get_vadjustment().set_value(0)
        later(200, loan_sync_audit_click)

    def loan_sync_audit_click():
        w = window[0]
        f = loan_sync_fixture
        rows = [node for node in walk(w._sync_sources_box) if isinstance(node, Adw.ActionRow)]
        kobo = next(row for row in rows if row.get_title() == "Kobo audit hors ligne")
        assert "Connexion : Déconnectée" in kobo.get_subtitle()
        audit = next(
            button for button in walk(kobo)
            if isinstance(button, Gtk.Button)
            and button.get_tooltip_text() == (
                "Audit SHA local de ce compte (lecture seule, peut être long)"
            )
        )
        assert audit.get_sensitive() and audit.get_mapped()
        assert not f["audit_calls"] and not f["mutations"]
        report["checks"]["P0_kobo_audit_offline_button_no_implicit_scan"] = True
        # A physical XTEST action is the sole trigger; rendering the row does
        # not authorize or start any inspection.
        report["pointer_actions"].append(device[0].click_widget(audit))
        f["audit_deadline"] = monotonic() + 6
        later(100, loan_sync_audit_result)

    def loan_sync_audit_result():
        w = window[0]
        f = loan_sync_fixture
        audit_row = next(
            row for row in walk(w._sync_sources_box)
            if isinstance(row, Adw.ActionRow)
            and row.get_title() == "Vérification des fichiers et rapprochements"
        )
        subtitle = audit_row.get_subtitle()
        if "Fichiers locaux vérifiés par SHA : 3" not in subtitle:
            assert monotonic() < f["audit_deadline"], (
                "Le résultat GTK de l'audit Kobo n'est pas apparu"
            )
            later(100, loan_sync_audit_result)
            return
        assert f["audit_calls"] == ["p0-kobo-audit"]
        assert "groupes de doublons binaires : 1" in subtitle
        assert "Audit partiel" in subtitle
        assert "Aucune réconciliation, acquisition ni suppression autorisée" in subtitle
        assert "fictitious-credential" not in subtitle
        assert not f["mutations"] and w.service.current_identity.identity_id == "camille"
        report["checks"]["P0_kobo_audit_real_gtk_explicit_offline_read_only"] = True
        report["checks"]["P0_kobo_audit_real_gtk_bounded_redacted_partial_result"] = True
        capture("03j-kobo-offline-explicit-audit")
        cleanup_loan_sync()
        later(100, continue_baseline_settings)

    def cleanup_loan_sync():
        f = loan_sync_fixture
        if not f.get("overrides"):
            return
        w = window[0]
        dialog = f.get("dialog")
        if dialog is not None and dialog.get_visible():
            dialog.close()
        for owner, name, existed, original in reversed(f["overrides"]):
            if existed:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
        f["overrides"] = []
        # Revoke fixture callbacks by rebuilding with the original broker (or
        # None for isolated shell contributions), then restore its exact owner.
        if f.get("proxy") is not None:
            f["proxy"].active = False
            w._refresh_sync_page()
        w.plugin_manager = f["manager"]
        w._populate_loans(f["loans"])
        w._displayed_loan = f["displayed"]
        if f["selected"] is not None:
            assert w.loan_page.select_key(f["selected"])

    def continue_baseline_settings():
        w = window[0]
        assert w.service.current_identity.identity_id == "camille"
        # Reuse both existing 1366/1600 MainWindow/XTEST baselines.
        # No extra workflow/scenario, provider activation or HTTP request.
        report["pointer_actions"].append(
            device[0].click_widget(w.nav_rows["settings"])
        )
        await_route("settings", check_settings_shell)

    SETTINGS_SECTIONS = (
        "accounts", "library", "sync", "interface", "ai", "diagnostic",
    )

    def settings_rows(section, kind):
        return [node for node in walk(section) if isinstance(node, kind)]

    def check_settings_shell():
        w = window[0]
        stack = w._settings_tabs
        assert stack.get_visible_child_name() == "accounts"
        assert w._settings_main_lifetime.alive
        assert all(stack.get_child_by_name(key) is not None
                   for key in SETTINGS_SECTIONS)
        report["checks"]["settings_six_real_gtk_sections"] = True
        inspect_settings_section(0)

    def inspect_settings_section(index):
        w = window[0]
        if index == len(SETTINGS_SECTIONS):
            begin_settings_persisted_edit()
            return
        key = SETTINGS_SECTIONS[index]
        w._settings_tabs.set_visible_child_name(key)
        later(130, lambda: assert_settings_section(index, key))

    def assert_settings_section(index, key):
        w = window[0]
        assert w.view_stack.get_visible_child_name() == "settings"
        assert w._settings_tabs.get_visible_child_name() == key
        section = w._settings_tabs.get_visible_child()
        assert section.get_mapped() and section.get_visible(), (
            "Onglet Paramètres non rendu", key
        )
        # The outer scroller must fit at 1366 and 1600 px. The horizontal
        # tab-switcher scroller is intentionally independent from this check.
        viewport = w.view_stack.get_child_by_name("settings")
        adjustment = viewport.get_hadjustment()
        overflow = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        assert adjustment.get_page_size() > 100, ("Viewport sans allocation", key)
        assert overflow < 5, ("Débordement horizontal Paramètres", key, overflow)
        checks = {
            "accounts": lambda: (
                len([row for row in settings_rows(section, Adw.EntryRow)
                     if row.get_title() == "Nom affiché"]) == 2
                and len([row for row in settings_rows(section, Adw.ActionRow)
                         if row.get_title() ==
                         "Parcours normal · Paris principal et ADEPT"]) == 2
            ),
            "library": lambda: any(
                row.get_title() == "Répertoire global"
                for row in settings_rows(section, Adw.EntryRow)
            ),
            "sync": lambda: any(
                row.get_title() == "Autoriser les téléchargements Kobo au démarrage"
                for row in settings_rows(section, Adw.ActionRow)
            ),
            "interface": lambda: any(
                row.get_title() == "Actions expérimentales"
                for row in settings_rows(section, Adw.ActionRow)
            ),
            "ai": lambda: any(
                row.get_title() == "Modèle installé sur le serveur"
                for row in settings_rows(section, Adw.EntryRow)
            ),
            "diagnostic": lambda: any(
                row.get_title() == "Diagnostic système"
                for row in settings_rows(section, Adw.PreferencesGroup)
            ),
        }
        assert checks[key](), ("Widgets Paramètres absents", key)
        report["checks"]["settings_" + key + "_rendered"] = True
        capture("08-settings-" + key)
        later(75, lambda: inspect_settings_section(index + 1))

    # P0 #1127: a physical GTK Save click must persist a harmless setting
    # to the E2E runner's disposable XDG_CONFIG_HOME; no provider or network.
    def begin_settings_persisted_edit():
        w = window[0]
        w._settings_tabs.set_visible_child_name("interface")
        later(180, change_persisted_interface_switch)

    def change_persisted_interface_switch():
        w = window[0]
        section = w._settings_tabs.get_visible_child()
        row = next((node for node in settings_rows(section, Adw.ActionRow)
                    if node.get_title() == "Actions expérimentales"), None)
        assert row is not None and row.get_mapped()
        switches = settings_rows(row, Gtk.Switch)
        assert len(switches) == 1 and switches[0].get_mapped()
        setting = switches[0]
        old_value = bool(core.config.get("experimental_actions", False))
        assert setting.get_active() == old_value
        report["settings_original_experimental_actions"] = old_value
        report["pointer_actions"].append(device[0].click_widget(setting))
        later(140, lambda: save_persisted_interface_change(setting, old_value))

    def save_persisted_interface_change(setting, old_value):
        w = window[0]
        assert setting.get_active() is (not old_value), "GTK Switch not toggled by XTEST"
        assert bool(core.config.get("experimental_actions", False)) == old_value, (
            "Unsaved GTK change leaked to live Core configuration"
        )
        viewport = w.view_stack.get_child_by_name("settings")
        saves = [node for node in settings_rows(viewport, Gtk.Button)
                 if node.get_tooltip_text() ==
                 "Enregistrer les modifications de configuration en attente"]
        assert len(saves) == 2, "Missing desktop/footer Save actions"
        save = next((node for node in saves
                     if node.get_mapped() and node.get_sensitive()), None)
        assert save is not None, "Edited Interface tab cannot be saved"
        checks_before = broker.checks
        queries_before = len(broker.event_queries)
        report["pointer_actions"].append(device[0].click_widget(save))
        deadline = monotonic() + 12

        def saved():
            # Check the **durable JSON**, not merely the Gtk.Switch state.
            try:
                on_disk = json.loads(core.config.path.read_text(encoding="utf-8"))
                persisted = on_disk.get("experimental_actions")
            except (OSError, ValueError):
                persisted = None
            target = not old_value
            ui_finished = all(not node.get_sensitive() for node in saves)
            if persisted is target and ui_finished:
                assert core.config.get("experimental_actions") is target
                assert broker.checks == checks_before
                assert len(broker.event_queries) == queries_before
                report["checks"]["settings_real_gtk_save_persists_xdg_json"] = True
                capture("08f-settings-saved-interface")
                begin_settings_reconnect_proof()
            elif monotonic() >= deadline:
                raise AssertionError(
                    "GTK Save did not commit an isolated Interface setting",
                    persisted, target, ui_finished,
                )
            else:
                later(100, saved)
        later(150, saved)

    # P0 #1127: real GTK Reconnect is an explicit Save-then-Reconnect,
    # even without pending edits. Intercept only the final provider invocation
    # so this offline recipe cannot contact any configured source.
    def begin_settings_reconnect_proof():
        w = window[0]
        w._settings_tabs.set_visible_child_name("accounts")
        later(180, click_settings_reconnect)

    def click_settings_reconnect():
        w = window[0]
        assert w._settings_tabs.get_visible_child_name() == "accounts"
        viewport = w.view_stack.get_child_by_name("settings")
        saves = [node for node in settings_rows(viewport, Gtk.Button)
                 if node.get_tooltip_text() ==
                 "Enregistrer les modifications de configuration en attente"]
        assert len(saves) == 2 and all(not node.get_sensitive() for node in saves), (
            "Reconnect baseline must not have any pending Settings edits"
        )
        reconnects = [node for node in settings_rows(viewport, Gtk.Button)
                      if node.get_tooltip_text() ==
                      "Enregistrer les modifications en attente puis reconnecter les sources"]
        assert len(reconnects) == 2, "Missing desktop/footer Reconnect"
        button = next((node for node in reconnects
                       if node.get_mapped() and node.get_sensitive()), None)
        assert button is not None, "Reconnect must be offered from Accounts"
        initial_config = core.config.path.read_bytes()
        expected_identities = core.config.identities()
        checks_before = broker.checks
        events_before = len(broker.event_queries)
        reconnect_calls = []
        original_reconnect = w.reconnect_sources

        def offline_reconnect():
            # Invoked only after the save worker has completed and its callback
            # has applied the result. No provider, token, or HTTP call occurs.
            reconnect_calls.append((
                core.config.path.read_bytes(), core.config.identities(),
            ))

        w.reconnect_sources = offline_reconnect
        report["pointer_actions"].append(device[0].click_widget(button))
        deadline = monotonic() + 12

        def check_reconnect():
            if reconnect_calls:
                w.reconnect_sources = original_reconnect
                assert len(reconnect_calls) == 1, "Duplicate Reconnect requests"
                assert reconnect_calls[0][0] == initial_config, (
                    "Unchanged Settings persisted differently before Reconnect"
                )
                assert reconnect_calls[0][1] == expected_identities
                assert broker.checks == checks_before
                assert len(broker.event_queries) == events_before
                report["checks"]["settings_real_gtk_reconnect_after_save_offline"] = True
                capture("08i-settings-reconnect-offline")
                begin_settings_unsaved_edit()
            elif monotonic() >= deadline:
                w.reconnect_sources = original_reconnect
                raise AssertionError(
                    "GTK Reconnect did not follow successful Settings commit"
                )
            else:
                later(100, check_reconnect)
        later(140, check_reconnect)

    def begin_settings_unsaved_edit():
        w = window[0]
        stack = w._settings_tabs
        stack.set_visible_child_name("accounts")
        later(130, settings_unsaved_edit)

    def settings_unsaved_edit():
        w = window[0]
        section = w._settings_tabs.get_visible_child()
        label_rows = [
            row for row in settings_rows(section, Adw.EntryRow)
            if row.get_title() == "Nom affiché"
        ]
        assert len(label_rows) == 2
        original = label_rows[0].get_text()
        expected_config = core.config.identities()[0]["label"]
        assert original == expected_config
        # No provider call and no save: dirty state must be a GTK-only draft.
        label_rows[0].set_text(original + " [brouillon E2E]")
        assert core.config.identities()[0]["label"] == expected_config
        report["checks"]["settings_unsaved_identity_draft_not_persisted"] = True
        assert label_rows[0].grab_focus()
        device[0].key("Tab", True); device[0].key("Tab", False)
        later(190, settings_dirty_and_keyboard)

    def settings_dirty_and_keyboard():
        w = window[0]
        assert w.get_focus() is not None, "Focus clavier perdu sur Paramètres"
        viewport = w.view_stack.get_child_by_name("settings")
        saves = [
            b for b in settings_rows(viewport, Gtk.Button)
            if (b.get_tooltip_text() or "")
            == "Enregistrer les modifications de configuration en attente"
        ]
        assert len(saves) == 2, ("Boutons Enregistrer desktop/footer", len(saves))
        assert any(b.get_sensitive() for b in saves), "Brouillon non détecté"
        assert core.config.identities()[0]["label"] == "Camille"
        report["checks"]["settings_keyboard_and_dirty_actions"] = True
        # Separate standalone Settings lifetime from the mapped main page.
        main_lifetime = w._settings_main_lifetime
        first = w._build_settings_page(standalone=True)
        first_lifetime = w._settings_standalone_lifetime
        assert isinstance(first, Gtk.Widget) and first_lifetime.alive
        second = w._build_settings_page(standalone=True)
        assert isinstance(second, Gtk.Widget)
        assert not first_lifetime.alive and w._settings_standalone_lifetime.alive
        assert w._settings_main_lifetime is main_lifetime and main_lifetime.alive
        report["checks"]["settings_standalone_rebuild_revokes_old_callbacks"] = True
        # P0 #1127: the two unmounted pages above test reconstruction,
        # but only a real mapped Adw.Window exercises the close-request
        # signal and releases its callback lifetime.
        second_lifetime = w._settings_standalone_lifetime
        w._open_prelogin_diagnostics()
        standalone = w._standalone_settings_window
        assert isinstance(standalone, Adw.Window)
        assert second_lifetime is not w._settings_standalone_lifetime
        assert not second_lifetime.alive
        actual_lifetime = w._settings_standalone_lifetime
        assert actual_lifetime.alive
        later(240, lambda: settings_standalone_mapped(
            standalone, actual_lifetime, main_lifetime
        ))

    def settings_standalone_mapped(standalone, lifetime, main_lifetime):
        w = window[0]
        assert standalone.get_mapped(), "Fenêtre autonome Paramètres non affichée"
        assert w._standalone_settings_window is standalone
        assert w._settings_standalone_lifetime is lifetime and lifetime.alive
        assert w._settings_main_lifetime is main_lifetime and main_lifetime.alive
        assert w.view_stack.get_visible_child_name() == "settings"
        assert core.config.identities()[0]["label"] == "Camille"
        report["checks"]["settings_standalone_real_gtk_window_mapped"] = True
        capture("08g-settings-standalone-window")
        closers = [
            node for node in walk(standalone)
            if isinstance(node, Gtk.Button) and node.get_tooltip_text() == "Fermer"
        ]
        assert closers, "Bouton de fermeture GTK autonome introuvable"
        report["pointer_actions"].append(device[0].click_widget(closers[0]))
        later(220, lambda: settings_standalone_closed(lifetime, main_lifetime))

    def settings_standalone_closed(lifetime, main_lifetime):
        w = window[0]
        assert not lifetime.alive, "Callbacks Paramètres toujours actifs après fermeture"
        assert w._standalone_settings_window is None
        assert w._settings_standalone_lifetime is None
        assert w._settings_main_lifetime is main_lifetime and main_lifetime.alive
        assert w.view_stack.get_visible_child_name() == "settings"
        assert core.config.identities()[0]["label"] == "Camille"
        assert broker.checks == 1
        report["checks"]["settings_standalone_real_close_revokes_callbacks"] = True
        capture("08h-settings-after-standalone-close")
        report["pointer_actions"].append(
            device[0].click_widget(w.nav_rows["library"])
        )
        await_route("library", settings_returned_to_library)

    def settings_returned_to_library():
        w = window[0]
        assert w.nav_list.get_selected_row() is w.nav_rows["library"]
        assert w.service.current_identity.identity_id == "camille"
        assert core.config.identities()[0]["label"] == "Camille"
        assert broker.checks == 1, "Naviguer Paramètres a relancé Diagnostics"
        report["checks"]["settings_return_keeps_identity_and_no_extra_diagnostics"] = True
        capture("09-library-after-settings")
        assert w.identity_dropdown.grab_focus()
        device[0].key("Tab", True); device[0].key("Tab", False)
        later(180, focus_after_tab)

    def focus_after_tab():
        w = window[0]
        assert w.get_focus() is not None
        report["checks"]["window_keyboard_focus"] = True
        assert w.identity_dropdown.grab_focus()
        device[0].key("space", True); device[0].key("space", False)
        later(220, lambda: select_identity_key("End", verify_second_identity))

    def select_identity_key(key, callback):
        for k in [key, "Return"]:
            device[0].key(k, True); device[0].key(k, False)
        later(500, callback)

    def verify_second_identity():
        w = window[0]
        assert w.service.current_identity.identity_id == "alex"
        assert w.identity_dropdown.get_selected() == 1
        assert w.view_stack.get_visible_child_name() == "library"
        report["checks"]["physical_second_identity_context"] = True
        capture("06-second-identity-library")
        assert w.identity_dropdown.grab_focus()
        device[0].key("space", True); device[0].key("space", False)
        later(220, lambda: select_identity_key("Home", verify_first_identity))

    def verify_first_identity():
        w = window[0]
        assert w.service.current_identity.identity_id == "camille"
        assert w.identity_dropdown.get_selected() == 0
        assert w.view_stack.get_visible_child_name() == "library"
        report["checks"]["physical_identity_context_restored"] = True
        capture("07-first-identity-restored")
        # P0 #1130: an orphaned history entry must not leak its old query
        # into the currently selected identity or navigate away from Library.
        # This uses the real GTK MainWindow/controls; no identity is deleted,
        # no source is contacted, and the missing ID is entirely fictional.
        from dataclasses import replace
        assert w.service.identity_by_id("ux-removed-identity") is None
        original_search = w.search_controls.state()
        original_library = w.library_controls.state()
        original_route = w.view_stack.get_visible_child_name()
        checks_before = broker.checks
        events_before = len(broker.event_queries)
        # Reuse MainWindow's existing dataclass types instead of adding
        # new direct module imports to the E2E dependency-selection graph.
        orphaned_state = replace(
            w._navigation_state("search"),
            identity_id="ux-removed-identity",
            query_state=replace(
                original_search,
                text="UX_STALE_IDENTITY_FILTER_MUST_NOT_APPEAR",
                providers=("local",),
            ),
        )
        w._restore_navigation_state(orphaned_state)
        assert w.service.current_identity.identity_id == "camille"
        assert w.view_stack.get_visible_child_name() == original_route == "library"
        assert w.nav_list.get_selected_row() is w.nav_rows["library"]
        assert w.search_controls.state() == original_search
        assert w.library_controls.state() == original_library
        assert not w._restoring_navigation
        assert broker.checks == checks_before
        assert len(broker.event_queries) == events_before
        report["checks"]["P0_stale_identity_history_preserves_gtk_filters"] = True
        capture("07b-stale-identity-history-rejected")
        begin_sync_progress_checks()

    def begin_sync_progress_checks():
        # Acceptance for #1132: real MainWindow, TaskRunner worker and GTK
        # footer. Only the provider operation is an offline fixture. Hold it
        # open until GTK has actually displayed the producer's identity text.
        w = window[0]
        sync_progress_check["original"] = w.service.opening_sync
        start_sync_progress_mode(False)

    def start_sync_progress_mode(deep):
        import threading
        w = window[0]
        deadline = monotonic() + 8

        def launch_when_idle():
            if w._active_tasks:
                assert monotonic() < deadline, "P0: previous UI tasks did not finish"
                later(60, launch_when_idle)
                return
            gate = threading.Event()
            sync_progress_check["gate"] = gate

            def offline_sync(progress=None, *, deep=False):
                sync_progress_check["callback"] = progress
                progress({"event": "identity_begin", "label": "Camille"})
                assert gate.wait(8), "P0: sync progress fixture was not released"
                return {"identities": []}

            w.service.opening_sync = offline_sync
            w.run_opening_sync(deep=deep)
            await_progress()

        def await_progress():
            label = w.busy_label.get_text()
            if (label == "En cours · Synchronisation · Camille…"
                    and w.busy_label.get_mapped() and w.busy_revealer.get_reveal_child()):
                assert w.view_stack.get_visible_child_name() == "library"
                assert len(w._active_tasks) == 1
                task_id, (_priority, _title, kind) = next(iter(w._active_tasks.items()))
                assert kind == ("deep-sync" if deep else "opening-sync")
                report["checks"]["P0_real_sync_identity_progress_deep" if deep
                                 else "P0_real_sync_identity_progress_normal"] = True
                capture("10-sync-progress-deep" if deep else "10-sync-progress-normal")
                if deep:
                    # Keep the real worker pending while closing the real
                    # GTK window; subsequent producer/idle callbacks are inert.
                    sync_progress_check["closing_pending_task"] = task_id
                    close_baseline_window()
                else:
                    sync_progress_check["gate"].set()
                    later(60, lambda: await_completion(task_id))
                return
            assert monotonic() < deadline, "P0: identity progress hidden: " + label
            later(60, await_progress)

        def await_completion(task_id):
            if task_id not in w._finished_task_ids:
                assert monotonic() < deadline, "P0: normal sync did not finish"
                later(60, lambda: await_completion(task_id))
                return
            sync_progress_check["callback"]({
                "event": "identity_begin", "label": "UX_STALE_SYNC_PROGRESS",
            })
            later(100, verify_stale_progress)

        def verify_stale_progress():
            assert "UX_STALE_SYNC_PROGRESS" not in w.busy_label.get_text()
            report["checks"]["P0_finished_sync_progress_cannot_resurrect_footer"] = True
            start_sync_progress_mode(True)

        launch_when_idle()

    def close_baseline_window():
        w = window[0]
        # P0 #1130: retain one *real GTK* plugin action/button before closing
        # the owner, as a plugin could retain an action from an older route.
        late_action_calls = []
        class RetainedPluginAction:
            title = "Action fictive tardive"
            icon_name = "document-open-symbolic"
            def callback(self, host):
                late_action_calls.append(host)
        public_host = w._UiHost(w)
        action_bar = public_host.build_action_bar([RetainedPluginAction()])
        action_buttons = [
            node for node in walk(action_bar) if isinstance(node, Gtk.Button)
        ]
        assert len(action_buttons) == 1
        retained_action_button = action_buttons[0]
        public_host.action_context()  # Real library context, before teardown.
        listeners_before = tuple(getattr(w, "_overview_listeners", ()))
        w.close()
        assert w._closed
        pending_sync = sync_progress_check.get("closing_pending_task")
        assert pending_sync is not None
        sync_progress_check["callback"]({
            "event": "identity_begin", "label": "UX_CLOSED_SYNC_PROGRESS",
        })
        sync_progress_check["gate"].set()
        w.service.opening_sync = sync_progress_check["original"]
        assert "UX_CLOSED_SYNC_PROGRESS" not in w.busy_label.get_text()
        report["checks"]["P0_close_during_real_sync_revokes_progress"] = True
        # P0 #1130/#1132: late plugin notifications are a no-op on an
        # actual closed GTK MainWindow, even if its manager is unavailable.
        # This is a synthetic post-close callback, not a provider operation.
        class ClosedPluginManager:
            def __getattr__(self, name):
                raise AssertionError(
                    "plugin manager accessed after GTK closure: " + name
                )
        manager_before = w.plugin_manager
        shell_before = w.shell_header
        plugin_widgets_before = dict(w._plugin_page_widgets)
        w.plugin_manager = ClosedPluginManager()
        try:
            w._plugins_changed()
            w._subscribe_cached_book_details()
        finally:
            w.plugin_manager = manager_before
        assert w.shell_header is shell_before
        assert w._plugin_page_widgets == plugin_widgets_before
        assert w._closed
        report["checks"]["P0_closed_plugin_callbacks_do_not_rebuild_gtk_shell"] = True
        # The retained GTK signal must not invoke a plugin callback, even
        # though the button and the public UiHost adapter remain referenced.
        retained_action_button.emit("clicked")
        assert late_action_calls == []
        assert public_host.search_books("Loup") is False
        assert public_host.open_book_detail(None) is False
        assert public_host.identity_overview() == []
        assert public_host.plugin_statuses() == []
        assert tuple(getattr(w, "_overview_listeners", ())) == listeners_before
        cancel = public_host.subscribe_identity_overview(
            lambda: late_action_calls.append("observer")
        )
        cancel()
        assert tuple(getattr(w, "_overview_listeners", ())) == listeners_before
        public_host.navigate("search")
        public_host.open_plugin_settings("fake-closed-plugin")
        public_host.open_identity_library("camille")
        public_host.open_identity_view("camille", route="library")
        public_host.import_acsm()
        public_host.synchronize()
        public_host.refresh_context()
        public_host.toast("Late plugin error must be discarded")
        assert late_action_calls == []
        assert w.view_stack.get_visible_child_name() == "library"
        assert w.shell_header is shell_before
        assert w._closed
        report["checks"]["P0_closed_public_plugin_host_and_GTK_button_are_inert"] = True
        # P0 #1129: exercise the actual MainWindow facade after GTK teardown,
        # not only its public plugin host or isolated AST methods. A retained
        # book link must neither queue selection nor change filters/route.
        pending_before = (
            getattr(w, "_pending_library_selection_key", None),
            getattr(w, "_pending_library_selection_identity_id", None),
            getattr(w, "_pending_library_selection_generation", None),
        )
        search_before = w.search_controls.state()
        library_before = w.library_controls.state()
        checks_before = broker.checks
        events_before = len(broker.event_queries)
        assert w.open_book_detail(None) is False
        assert w._open_library_item("ux-removed-book:private") is None
        assert w._select_pending_library_item() is False
        assert (
            getattr(w, "_pending_library_selection_key", None),
            getattr(w, "_pending_library_selection_identity_id", None),
            getattr(w, "_pending_library_selection_generation", None),
        ) == pending_before
        assert w.search_controls.state() == search_before
        assert w.library_controls.state() == library_before
        assert w.view_stack.get_visible_child_name() == "library"
        assert broker.checks == checks_before
        assert len(broker.event_queries) == events_before
        report["checks"]["P0_closed_MainWindow_book_links_keep_gtk_context"] = True
        from cifereca.task_queue import TaskPriority
        try:
            w.runner.pool.submit(TaskPriority.NORMAL, lambda: None)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Closed task runner accepted new work")
        assert w.service._virtual_collection_closed
        report["checks"]["normal_close_releases_runner_and_service"] = True
        finish()

    def finish():
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsNavigationUXE2E")

        def do_activate(self):
            try:
                w = MainWindow(self, core.config, core.library, core.diagnostics)
                window[0] = w
                w.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
                w.plugin_manager = IsolatedContributions(page_plugin)
                w._rebuild_plugin_pages()
                w.present()
                device[0] = X11Input()
                later(500, initial)
            except Exception as exc:
                fail(f"Activation MainWindow: {type(exc).__name__}: {exc}")

        def do_shutdown(self):
            cleanup_constellations()
            cleanup_loan_sync()
            if device[0] is not None:
                device[0].close()
            page_plugin.deactivate()
            core.close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        50, lambda: (fail("Timeout Diagnostics navigation 50s"), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="GTK fermé sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
