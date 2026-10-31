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
from cifereca.gui.sync_action_layout import sync_action_row
from cifereca.builtin_ui.cifereca_builtin_ui import Pages
from diagnostics_page_probe import Broker, Context, Host, Tasks
from diagnostics_states_probe import StateTasks
from x11_input import X11Input
from gtk_layout_critical import GTK_PHASES_2027



def gtk_phase_2027(phase: str) -> None:
    # Source-reviewed constants only; no widget, pointer or account data.
    assert phase in GTK_PHASES_2027
    marker = f"CIFERECA_E2E_GTK_PHASE:{phase}"
    print(marker, file=sys.stdout, flush=True)
    print(marker, file=sys.stderr, flush=True)


def probe_failure_line(exc):
    """Return only the deepest line in this probe, never exception data."""
    line = None
    trace = exc.__traceback__
    while trace is not None:
        if trace.tb_frame.f_code.co_filename == __file__:
            line = trace.tb_lineno
        trace = trace.tb_next
    return line


def probe_failure_location(exc):
    """Closed source ID + deepest reviewed source line, never traceback data."""
    allowed = {__file__: "navigation", str(ROOT / "e2e/activity_feedback_acceptance.py"): "activity_feedback"}
    location = None
    trace = exc.__traceback__
    while trace is not None:
        source = allowed.get(trace.tb_frame.f_code.co_filename)
        if source is not None:
            location = (source, trace.tb_lineno)
        trace = trace.tb_next
    return location


def present_offline_fixture_error(present_error, error, *, dialogs):
    """Tag only a new dialog proven to originate in this exact offline fixture."""
    from cifereca.providers import ProviderConfigurationError
    before = set(dialogs())
    present_error(error)
    expected = (type(error) is ProviderConfigurationError
                and error.args == ("Provider non connecté: local",))
    for dialog in set(dialogs()) - before:
        if expected and dialog.get_body() == "Traitement indisponible":
            dialog._cifereca_attested_offline_fixture = True


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
    coordination = {}

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
        cleanup_sync_coordination()
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
                # Local harness checkpoint only. Publication validates this
                # integer against this source's function/lambda declarations.
                code = getattr(callback, "__code__", None)
                if code is not None and code.co_filename == __file__:
                    report["checkpoint_line"] = code.co_firstlineno
                    report.pop("checkpoint_source", None)
                elif code is not None and code.co_filename == str(ROOT / "e2e/activity_feedback_acceptance.py"):
                    report["checkpoint_line"] = code.co_firstlineno
                    report["checkpoint_source"] = "activity_feedback"
                callback()
            except Exception as exc:
                location = probe_failure_location(exc)
                report["failure_line"] = location[1] if location is not None else None
                if location is not None and location[0] == "activity_feedback":
                    report["failure_source"] = "activity_feedback"
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
        minimum = w.shell_header.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        assert minimum <= 760, ("Header prevents supported narrow window", minimum)
        assert "prêts de bibliothèque" in w.statistics_button.get_label()
        report["checks"]["P1_2008_header_counter_shrinks_without_semantic_abbreviation"] = True
        report["header_minimum_width"] = minimum
        assert not w.nav_rows["diagnostics"].get_mapped(), "Configuration doit être repliée"
        report["checks"]["real_mainwindow_and_plugin_contribution"] = True
        capture("00-real-cifereca-shell")
        report["pointer_actions"].append(device[0].click_widget(w.nav_rows["library"]))
        await_route("library", library_open)

    def home_summary_acceptance():
        from home_summary_acceptance import check_home_summary
        check_home_summary(
            window[0], later=later, capture=capture, report=report, done=initial,
        )

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
        from activity_feedback_acceptance import exercise
        exercise(w, Gtk=Gtk, Adw=Adw, walk=walk, device=device[0], report=report,
                 capture=capture, later=later, done=begin_library_volume)

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
        # Capture the actual concurrent.futures.Future without changing
        # the worker or TaskRunner implementation. This distinguishes a
        # worker still resolving from a GTK idle callback not yet delivered.
        original_submit = w.runner.pool.submit
        def trace_submit(priority, operation):
            future = original_submit(priority, operation)
            f["latest_task_future"] = future
            return future
        override(w.runner.pool, "submit", trace_submit)
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
            *[SyncSourceRef(
                f"p0-kobo-target-{number}", "camille", "kobo",
                f"fictitious-target-{number}", f"Kobo ciblé {number}",
                sync_capabilities=("local_materialization",),
            ) for number in (1, 2, 3)],
            SyncSourceRef("p0-numilog-inventory", "camille", "numilog", "fictitious-numilog",
                          "Achats Numilog fictifs", sync_capabilities=("remote_inventory", "entitlements")),
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

        # CoreUI acceptance boundary: real widgets and TaskRunner, synthetic
        # broker responses only. No provider, file or DRM success is claimed.
        from threading import Event
        f["prepare_calls"] = []
        f["materialize_calls"] = []
        f["download_release"] = Event()
        f["download_started"] = Event()
        f["cancel_worker_entered"] = Event()
        f["cancel_worker_woke"] = Event()
        f["cancel_worker_returning"] = Event()
        def prepare_materialization(source_id):
            assert source_id == "p0-kobo-target-2", "Préparation sur un autre compte"
            f["prepare_calls"].append(source_id)
            counts = dict.fromkeys((
                "already_local_linked", "already_local_verified",
                "needs_reconciliation", "candidate_unverified", "potentially_missing",
                "attention_required", "blocked_scope", "blocked_unlinked",
                "not_downloadable", "invalid_manifest_items",
            ), 0)
            counts.update(already_local_linked=1, potentially_missing=2)
            return NS(ok=True, changed=0, facts={
                "status": "prepared", "requires_explicit_confirmation": True,
                "acquisition_authorized": False, "manifest_refreshed": True,
                "confirmation_token": f"{len(f['prepare_calls']):064x}",
                "expires_in_seconds": 600, "counts": counts,
            })
        def materialize_one(source_id, token, *, control):
            assert source_id == "p0-kobo-target-2", "Acquisition sur un autre compte"
            assert token == f"{len(f['prepare_calls']):064x}"
            f["materialize_calls"].append(source_id)
            if len(f["materialize_calls"]) == 1:
                # Cross-thread milestones are intentionally enumerated and
                # contain no account names, paths, tokens or provider data.
                f["cancel_worker_entered"].set()
                f["download_started"].set()
                was_released = f["download_release"].wait(10)
                f["cancel_worker_woke"].set()
                assert was_released, "Annulation GTK absente"
                assert control.cancelled(), "Le contrôle n'a pas reçu l'annulation"
                f["cancel_worker_returning"].set()
                return NS(ok=False, changed=1, facts={"status": "cancelled"})
            assert not control.cancelled()
            return NS(ok=True, changed=1, facts={"status": "success"})
        f["inventory_policy"] = {"mode": "manual", "interval_minutes": 60}
        f["inventory_policy_writes"] = []
        f["numilog_preview_calls"] = []
        def numilog_preview(source_id, *, verify_local_files=False):
            assert source_id == "p0-numilog-inventory"
            f["numilog_preview_calls"].append((source_id, verify_local_files))
            counts = dict.fromkeys((
                "owned_purchases", "already_local_linked", "already_local_verified",
                "needs_file_verification", "potentially_missing", "candidate_unverified",
                "needs_reconciliation", "attention_required", "not_downloadable",
                "ignored_unattested_rights",
            ), 0)
            counts.update(owned_purchases=3, potentially_missing=2, candidate_unverified=2)
            return NS(ok=True, facts={
                "phase": "local_materialization", "status": "preview_only",
                "read_only": True, "offline": True, "acquisition_authorized": False,
                "confirmation_available": False, "schedule_available": False,
                "manifest_refreshed": False, "requires_manifest_validation": True,
                "files_verified": verify_local_files, "known_item_count": 3,
                "counts": counts, "private": "PRIVATE_NUMILOG_PREVIEW",
            })
        def inventory_policy(source_id):
            if source_id != "p0-numilog-inventory":
                raise ValueError("inventory schedule not supported by fixture")
            return dict(f["inventory_policy"])
        def set_inventory_policy(source_id, mode, *, interval_minutes):
            assert source_id == "p0-numilog-inventory"
            assert mode in {"manual", "periodic"} and interval_minutes == 60
            f["inventory_policy_writes"].append((source_id, mode))
            f["inventory_policy"] = {"mode": mode, "interval_minutes": interval_minutes}
            return dict(f["inventory_policy"])
        sync_broker = NS(
            preview_local_materialization=numilog_preview,
            inventory_refresh_schedule=inventory_policy,
            set_inventory_refresh_schedule=set_inventory_policy,
            sources=read_sources, synchronize=forbidden,
            synchronize_source=synchronize_one, set_enabled=forbidden,
            audit_local_reconciliation=audit_local_reconciliation,
            prepare_local_materialization=prepare_materialization,
            materialize_source=materialize_one,
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
        assert w.loan_filter == "all" and w.loan_filter_buttons["all"].get_active()
        assert not w.loan_languages.values()
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
        # #2141 acceptance: the initial page exposes status and one primary
        # source action, while account tools start closed. Preserve the full
        # expanded-page acceptance below after testing this new disclosure.
        w = window[0]
        f = loan_sync_fixture
        expanders = [node for node in walk(w._sync_sources_box)
                     if isinstance(node, Gtk.Expander)]
        assert len(expanders) == 5
        assert all(not node.get_expanded() for node in expanders)
        assert all(not node.get_child().get_mapped() for node in expanders)
        source_rows = [node for node in walk(w._sync_sources_box)
                       if isinstance(node, Adw.ActionRow)
                       and node.get_title() in {
                           "Kobo audit hors ligne", "Kobo ciblé 1", "Kobo ciblé 2",
                           "Kobo ciblé 3", "Source partielle fictive",
                           "Source déconnectée fictive", "Achats Numilog fictifs",
                       }]
        assert len(source_rows) == 7
        # #2141: a failed account must expose a high-contrast, account-local
        # indicator BEFORE its secondary tools are opened; no raw broker error
        # and no action/permission mutation accompanies the passive render.
        failed_paris = next(
            row for row in walk(w._sync_sources_box)
            if isinstance(row, Adw.ActionRow)
            and row.get_title() == "Paris homonyme · paris · connexion 2/2"
        )
        assert failed_paris.get_subtitle().startswith(
            "Synchronisation : échec — action nécessaire\n"
        )
        assert "PRIVATE_SYNC_ERROR" not in failed_paris.get_subtitle()
        # A local warning must never mask a harder error elsewhere in the
        # same account. This is a pure, closed-state view projection test;
        # it never contacts the broker or alters consent.
        from cifereca.gui.sync_view import sync_source_attention
        from types import SimpleNamespace
        mixed = SimpleNamespace(
            sync_capable=True, enabled=True, connected=True,
            status="failed", error="PRIVATE_SYNC_ERROR",
            phase_states=(
                SimpleNamespace(phase="materialize", status="partial"),
                SimpleNamespace(phase="discover_rights", status="failed"),
            ),
        )
        assert sync_source_attention(mixed) == (
            "error", "Notices et droits : échec — action nécessaire",
        )
        report["checks"]["P0_2141_hard_error_precedes_phase_warning"] = True
        alerts = [
            node for node in walk(failed_paris)
            if isinstance(node, Gtk.Image)
            and node.get_icon_name() == "dialog-error-symbolic"
        ]
        assert len(alerts) == 1
        report["checks"]["P0_2141_failed_account_indicator_visible_before_tools"] = True
        for row in source_rows:
            buttons = [node for node in walk(sync_action_row(row))
                       if isinstance(node, Gtk.Button)]
            assert len(buttons) == 1 and row.get_mapped()
            assert "Synchronisation :" in row.get_subtitle()
        backup = next(node for node in walk(w._sync_sources_box)
                      if isinstance(node, Adw.ActionRow)
                      and node.get_title() == "Sauvegarde automatique des achats Numilog")
        assert backup.get_mapped(), "Durable backup status must remain outside tools"
        assert "11 source(s)" in w._sync_summary.get_label()
        # #2141: the summary and actions must stay OUTSIDE the scrolling
        # source list, including after moving far below the first fold.
        assert w._sync_attention_row.get_visible()
        assert "À vérifier :" in w._sync_attention_text.get_label()
        assert w._sync_attention_icon.get_icon_name() == "dialog-error-symbolic"
        assert "PRIVATE_SYNC_ERROR" not in w._sync_attention_text.get_label()
        ancestor = w._sync_summary
        while ancestor is not None:
            assert not isinstance(ancestor, Gtk.ScrolledWindow), (
                "Source context must remain fixed above the scrolling list"
            )
            ancestor = ancestor.get_parent()
        report["checks"]["P0_2141_sticky_sync_context_and_closed_alert_totals"] = True
        f["sync_tools"] = expanders
        f["sync_tools_font"] = Gtk.Settings.get_default().get_property("gtk-font-name")
        scroll = w._sync_sources_box.get_parent()
        while not isinstance(scroll, Gtk.ScrolledWindow):
            scroll = scroll.get_parent()
        f["sync_scroll"] = scroll
        capture("03h-sync-collapsed-source-tools")
        w.set_default_size(1280, 850)
        later(350, lambda: sync_collapsed_desktop(False))

    def sync_collapsed_desktop(large_font):
        w = window[0]
        f = loan_sync_fixture
        assert w.get_width() <= 1300
        assert all(not node.get_expanded() for node in f["sync_tools"])
        assert not f["source_sync_calls"] and not f["prepare_calls"]
        assert not f["materialize_calls"] and not f["audit_calls"]
        capture("03h-sync-collapsed-1280-" + ("large" if large_font else "normal"))
        if not large_font:
            Gtk.Settings.get_default().set_property("gtk-font-name", "Sans 12")
            later(350, lambda: sync_collapsed_desktop(True))
            return
        Gtk.Settings.get_default().set_property("gtk-font-name", f["sync_tools_font"])
        w.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
        later(350, lambda: kobo_click_visible(
            f["sync_tools"][0].get_label_widget().get_parent().get_first_child(),
            sync_tools_opened,
        ))

    def sync_tools_opened():
        f = loan_sync_fixture
        # Observe GTK's disclosure allocation after the one physical click;
        # never replace it with set_expanded(), clicked() or an input retry.
        deadline = f.setdefault("sync_tools_deadline", monotonic() + 2)
        if not f["sync_tools"][0].get_expanded() and monotonic() < deadline:
            later(80, sync_tools_opened)
            return
        assert f["sync_tools"][0].get_expanded(), "XTEST must open account tools"
        assert not f["source_sync_calls"] and not f["prepare_calls"]
        assert not f["materialize_calls"] and not f["audit_calls"]
        report["checks"]["P0_2141_source_status_primary_action_and_explicit_tools"] = True
        # Prepare the remaining existing geometry/action tests. The first
        # disclosure above was physical XTEST; no business callback ran.
        for expander in f["sync_tools"]:
            expander.set_expanded(True)
        f["sync_scroll"].get_vadjustment().set_value(0)
        later(350, loan_sync_tools_rendered)

    def loan_sync_tools_rendered():
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
                   for node in walk(sync_action_row(offline)))
        assert w._sync_all_button.get_sensitive()
        assert f["source_queries"] and not f["mutations"]
        assert w.service.current_identity.identity_id == "camille"
        assert w.search_controls.state() == f["search_state"]
        assert w.library_controls.state() == f["library_state"]
        report["checks"]["P0_sync_real_gtk_partial_preserves_last_success_and_identity"] = True
        report["checks"]["P0_sync_real_gtk_disconnected_source_action_disabled"] = True
        capture("03i-sync-partial-last-success-and-offline-source")
        # #980 acceptance uses the real MainWindow/controller/widgets and
        # XTEST, with eleven synthetic broker rows; no provider/network/DRM.
        # Eleven sources, two Numilog read-only/inventory rows and five additional rows
        # per Kobo account: preview,
        # audit, confirmed materialization, independent acquisition startup
        # consent, and now the separate inventory/rights refresh consent.
        # The synthetic broker declines Kobo scheduling; those rows remain
        # inert. Numilog inventory consent is exercised separately below.
        # #1905 adds one distinct Numilog *book backup* consent row.
        # The synthetic broker has no consent APIs: the CTA must be inert.
        assert len(rows) == 34, ("Unexpected GTK sync action-row count", len(rows))
        numilog_backup_rows = [
            row for row in rows
            if row.get_title() == "Sauvegarde automatique des achats Numilog"
        ]
        assert len(numilog_backup_rows) == 1
        numilog_backup = numilog_backup_rows[0]
        assert "Sauvegarde planifiée indisponible" in numilog_backup.get_subtitle()
        assert 0 < numilog_backup.get_height() <= 420
        assert not any(
            isinstance(node, Gtk.Button) and node.get_sensitive()
            for node in walk(sync_action_row(numilog_backup))
        )
        report["checks"]["P0_1905_numilog_backup_consent_inert_without_broker"] = True
        inventory_schedule_rows = [
            row for row in rows
            if row.get_title() == "Actualisation automatique des notices Kobo"
        ]
        assert len(inventory_schedule_rows) == 4
        for inventory_row in inventory_schedule_rows:
            assert "Planification des notices indisponible" in inventory_row.get_subtitle()
            assert "Aucune synchronisation automatique" in inventory_row.get_subtitle()
            assert 0 < inventory_row.get_height() <= 420
            assert not any(
                isinstance(node, Gtk.Button) and node.get_sensitive()
                for node in walk(sync_action_row(inventory_row))
            )
        report["checks"]["P0_1346_inventory_schedule_is_separate_and_inert_without_broker"] = True
        schedule_row = next(row for row in rows
                            if row.get_title() == "Téléchargement automatique de ce compte")
        assert "Planification indisponible" in schedule_row.get_subtitle()
        assert not any(isinstance(node, Gtk.Button) and node.get_sensitive()
                       for node in walk(sync_action_row(schedule_row)))
        acquisition_row = next(row for row in rows
                               if row.get_title() == "Téléchargement local · confirmation obligatoire")
        assert "Non préparé" in acquisition_row.get_subtitle()
        assert "aucun fichier téléchargé" in acquisition_row.get_subtitle().lower()
        # A prior layout put six action buttons on the source ActionRow,
        # leaving its title one character wide and its height >3700px.
        # Assert the real allocated height in both GTK viewport widths.
        kobo_source = next(row for row in rows if row.get_title() == "Kobo audit hors ligne")
        assert 0 < kobo_source.get_height() <= 420, (
            "Kobo source row vertically collapsed by crowded actions",
            kobo_source.get_height(),
        )
        assert 0 < acquisition_row.get_height() <= 420, (
            "Kobo confirmation row vertically collapsed by crowded actions",
            acquisition_row.get_height(),
        )
        # P1 #1889: measure *real* GTK allocations, not only widget properties.
        # The source subtitles span multiple lines; suffix actions must keep
        # their natural command height, vertically centered even when disabled.
        suffix_allocations = []
        for sync_row in rows:
            for button in (node for node in walk(sync_action_row(sync_row))
                           if isinstance(node, Gtk.Button)):
                assert button.get_valign() == Gtk.Align.CENTER, (
                    "Uncentered Synchronisation suffix", sync_row.get_title(),
                    button.get_label(),
                )
                button_height = button.get_height()
                assert 0 < button_height <= 72, (
                    "Synchronisation suffix stretched to subtitle height",
                    sync_row.get_title(), button.get_label(),
                    button_height, sync_row.get_height(),
                )
                ok, bounds = button.compute_bounds(sync_action_row(sync_row))
                assert ok and bounds.get_y() >= 0, (
                    "Suffix outside ActionRow", sync_row.get_title(),
                )
                assert bounds.get_y() + bounds.get_height() <= sync_action_row(sync_row).get_height() + 1
                suffix_allocations.append({
                    "row_height": sync_row.get_height(),
                    "button_height": button_height,
                    "button_width": button.get_width(),
                    "valign": "center",
                })
        assert len(suffix_allocations) >= 20, (
            "Missing expected Kobo/Paris/public source suffixes",
            len(suffix_allocations),
        )
        report["sync_suffix_allocations"] = suffix_allocations
        report["checks"]["P1_1889_gtk_sync_suffix_natural_height_centered"] = True
        report["checks"]["P0_kobo_acquisition_controls_keep_source_row_compact"] = True
        report["checks"]["P0_kobo_acquisition_off_by_default_on_sync_page"] = True
        # #2141: public catalogue rows still expose their activation switch,
        # but no fake grey Sync CTA. The fixed two-line presentation is concise
        # and the ordering lets actual accounts occupy the first viewport.
        for provider in ("gutenberg", "gallica"):
            row = next(row for row in rows if row.get_title() == f"Catalogue public · {provider}")
            subtitle = row.get_subtitle()
            # The real Gutenberg fixture is disabled; Gallica is enabled.
            expected_activation = "Désactivée" if provider == "gutenberg" else "Activée"
            assert f"Recherche de catalogue distincte, si disponible · {expected_activation} · Connectée" in subtitle
            assert "Inventaire synchronisé non pris en charge" in subtitle
            assert subtitle.count("\n") == 1
            assert "Échec" not in subtitle
            assert any(isinstance(node, Gtk.Switch) for node in walk(sync_action_row(row)))
            assert not any(isinstance(node, Gtk.Button) for node in walk(sync_action_row(row)))
        paris = [next(row for row in rows
                      if row.get_title() == f"Paris homonyme · paris · connexion {i}/2")
                 for i in (1, 2)]
        assert "Dernier succès 10/10/2026 12:00" in paris[0].get_subtitle()
        assert "Synchronisation : Échec" in paris[1].get_subtitle()
        for row in paris:
            assert "Portée de cette source : " in row.get_subtitle()
            assert "observations de droits et prêts" in row.get_subtitle()
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
        # UX #2141: observation du premier écran de la vraie fenêtre GTK.
        # Métriques agrégées sans nom de compte, URL ni texte de fournisseur.
        # Ce relevé ne vaut pas validation de la lisibilité : pas de seuil PASS.
        source_groups = []
        group_child = w._sync_sources_box.get_first_child()
        while group_child is not None:
            if isinstance(group_child, Adw.PreferencesGroup):
                source_groups.append(group_child)
            group_child = group_child.get_next_sibling()
        # The two catalogue-only sources should no longer push accounts below
        # the fold. Keep this assertion on live GTK group ordering, not a mock.
        source_group_titles = []
        for group in source_groups:
            first = next((node for node in walk(group) if isinstance(node, Adw.ActionRow)), None)
            if first is not None:
                source_group_titles.append(first.get_title())
        assert len(source_group_titles) == 11
        assert all(title.startswith("Catalogue public · ") for title in source_group_titles[-2:])
        assert all(not title.startswith("Catalogue public · ") for title in source_group_titles[:-2])
        report["checks"]["P0_2141_sync_accounts_before_public_catalogues"] = True
        source_rows = []
        for group in source_groups:
            first_row = next(
                (node for node in walk(group) if isinstance(node, Adw.ActionRow)),
                None,
            )
            if first_row is not None:
                source_rows.append(first_row)

        def first_view_geometry(widget):
            # Count full allocation separately from a one-pixel intersection.
            # All measurements are numeric/boolean aggregates; never record
            # widget titles, account IDs, text, private paths or messages.
            ok, bounds = widget.compute_bounds(scroll)
            if not ok or not widget.get_mapped():
                return {"intersects": False, "fully_visible": False,
                        "clipped_horizontally": False}
            x, y = float(bounds.get_x()), float(bounds.get_y())
            width, height = float(bounds.get_width()), float(bounds.get_height())
            view_width, view_height = float(scroll.get_width()), float(scroll.get_height())
            intersects = bool(
                width > 0 and height > 0 and x < view_width
                and x + width > 0 and y < view_height and y + height > 0
            )
            return {
                "intersects": intersects,
                "fully_visible": bool(
                    intersects and x >= 0 and y >= 0
                    and x + width <= view_width + 0.5
                    and y + height <= view_height + 0.5
                ),
                "clipped_horizontally": bool(
                    intersects and (x < 0 or x + width > view_width + 0.5)
                ),
            }

        def in_first_view(widget):
            return first_view_geometry(widget)["intersects"]

        first_actions = []
        all_actions = []
        for row in rows:
            buttons = [
                node for node in walk(sync_action_row(row))
                if isinstance(node, Gtk.Button)
            ]
            all_actions.extend(buttons)
            if row in source_rows and buttons:
                first_actions.append(buttons[0])

        def sample_first_fold():
            # Recompute dimensions after every GTK reflow; 1366/1600 is
            # not evidence for 1280 or enlarged typography.
            adjustment = scroll.get_vadjustment()
            viewport = max(1.0, float(adjustment.get_page_size()))
            total_height = float(adjustment.get_upper())
            return {
                "viewport_width": int(scroll.get_width()),
                "viewport_height": int(scroll.get_height()),
                "window_width": int(w.get_width()),
                "window_height": int(w.get_height()),
                "source_cards": len(source_groups),
                "source_status_rows": len(source_rows),
                "detail_rows": len(rows) - len(source_rows),
                "source_summary_lines": sum(
                    (row.get_subtitle() or "").count("\n") + 1
                    for row in source_rows
                ),
                "action_buttons": len(all_actions),
                "primary_source_actions": len(first_actions),
                "visible_source_status_rows": sum(
                    in_first_view(row) for row in source_rows
                ),
                "visible_primary_source_actions": sum(
                    in_first_view(button) for button in first_actions
                ),
                "visible_action_buttons": sum(
                    in_first_view(button) for button in all_actions
                ),
                # UX P0 #2141: full visibility/activation is a stronger
                # descriptive signal than a one-pixel viewport intersection.
                # It still does not prove accessible labels or click success.
                "fully_visible_source_status_rows": sum(
                    first_view_geometry(row)["fully_visible"] for row in source_rows
                ),
                "fully_visible_primary_source_actions": sum(
                    first_view_geometry(button)["fully_visible"]
                    for button in first_actions
                ),
                "fully_visible_enabled_primary_source_actions": sum(
                    first_view_geometry(button)["fully_visible"] and button.get_sensitive()
                    for button in first_actions
                ),
                "fully_visible_action_buttons": sum(
                    first_view_geometry(button)["fully_visible"]
                    for button in all_actions
                ),
                "horizontally_clipped_primary_source_actions": sum(
                    first_view_geometry(button)["clipped_horizontally"]
                    for button in first_actions
                ),
                "global_action_fully_visible": first_view_geometry(
                    w._sync_all_button
                )["fully_visible"],
                "global_action_visible": in_first_view(w._sync_all_button),
                "scroll_content_height": round(total_height, 1),
                "scroll_extent_viewports": round(
                    max(0.0, total_height - viewport) / viewport, 2
                ),
            }
        f["sync_first_fold_sample"] = sample_first_fold
        report["sync_first_fold_ux"] = sample_first_fold()
        f["sync_original_font"] = Gtk.Settings.get_default().get_property("gtk-font-name")
        long_name_row = next(row for row in rows if row.get_title() == "Kobo audit hors ligne")
        f["sync_long_name_row"] = long_name_row
        f["sync_original_title"] = long_name_row.get_title()
        long_name_row.set_title("Compte familial de démonstration — plusieurs éditions homonymes et un nom volontairement long")
        # Keep the existing 760px assertions; collect UX evidence at the
        # product's target 1280px *before* shrinking further.
        w.set_default_size(1280, 850)
        later(350, lambda: sync_desktop_first_fold(False))

    def sync_desktop_first_fold(large_font):
        gtk_phase_2027("sync_desktop" if not large_font else "sync_font_desktop")
        w = window[0]
        f = loan_sync_fixture
        key = "large_font" if large_font else "normal_font"
        sample = f["sync_first_fold_sample"]()
        sample["requested_width"] = 1280
        sample["enlarged_font"] = large_font
        report.setdefault("sync_1280_first_fold_ux", {})[key] = sample
        # Screenshots are local/private E2E proof; only existing allowlisted
        # PASS/FAIL verdicts are published by the CI pipeline.
        capture("03i-sync-1280-" + key)
        if not large_font:
            Gtk.Settings.get_default().set_property("gtk-font-name", "Sans 12")
            later(350, lambda: sync_desktop_first_fold(True))
            return
        Gtk.Settings.get_default().set_property(
            "gtk-font-name", f["sync_original_font"]
        )
        gtk_phase_2027("sync_narrow")
        w.set_default_size(760, 780)
        deadline = monotonic() + 4

        def wait_sync_narrow():
            # set_default_size queues an asynchronous toplevel allocation.
            # #2203 observed width >760 despite all GTK minima <=760 after
            # the fixed 350ms delay. Wait for the requested width, as D16
            # already does; on deadline run the unchanged strict assertions.
            if w.get_width() <= 760 or monotonic() >= deadline:
                sync_narrow_allocations(False)
            else:
                later(80, wait_sync_narrow)
        later(80, wait_sync_narrow)

    def sync_narrow_allocations(large_font):
        w = window[0]
        f = loan_sync_fixture
        scroll = f["sync_scroll"]
        geometry = {
            "window_width": w.get_width(),
            "header_min": w.shell_header.measure(Gtk.Orientation.HORIZONTAL, -1)[0],
            "scroll_min": scroll.measure(Gtk.Orientation.HORIZONTAL, -1)[0],
            "sources_min": w._sync_sources_box.measure(Gtk.Orientation.HORIZONTAL, -1)[0],
            "source_rows": [
                {"title": row.get_title(), "minimum": sync_action_row(row).measure(Gtk.Orientation.HORIZONTAL, -1)[0]}
                for row in walk(w._sync_sources_box) if isinstance(row, Adw.ActionRow)
            ],
        }
        report["sync_minimum_width_geometry"] = geometry
        if w.get_width() > 760:
            # Closed failure categories only: distinguish a real minimum
            # request from an outstanding allocation, without publishing
            # account labels, widget dumps or native coordinates.
            if geometry["header_min"] > 760:
                report["stage"] = "P0_2027_narrow_header_minimum"
            elif geometry["sources_min"] > 760:
                report["stage"] = "P0_2027_narrow_source_minimum"
            elif geometry["scroll_min"] > 760:
                report["stage"] = "P0_2027_narrow_scroll_minimum"
            elif w.measure(Gtk.Orientation.HORIZONTAL, -1)[0] > 760:
                report["stage"] = "P0_2027_narrow_window_minimum"
            else:
                report["stage"] = "P0_2027_narrow_allocation_pending"
        assert w.get_width() <= 760, ("Synchronisation minimum window width", geometry)
        assert scroll.get_width() > 0
        allocations = []
        for row in (node for node in walk(w._sync_sources_box) if isinstance(node, Adw.ActionRow)):
            area = sync_action_row(row)
            assert area.get_width() <= scroll.get_width()
            assert 0 < row.get_height() <= 420, (row.get_title(), row.get_height())
            for button in (node for node in walk(area) if isinstance(node, Gtk.Button)):
                assert button.get_mapped() and 0 < button.get_height() <= 96
                ok, bounds = button.compute_bounds(area)
                assert ok and bounds.get_x() >= 0
                assert bounds.get_x() + bounds.get_width() <= area.get_width() + 1
                assert bounds.get_y() + bounds.get_height() <= area.get_height() + 1
                assert button.get_valign() == Gtk.Align.CENTER
                allocations.append({"row": row.get_title(), "height": button.get_height(),
                                    "width": button.get_width(), "area_width": area.get_width()})
        assert len(allocations) >= 20
        key = "large_font" if large_font else "normal_font"
        report.setdefault("sync_760_allocations", {})[key] = allocations
        report["checks"]["P1_1889_sync_760_" + key] = True
        report["stage"] = "P1_1889_sync_760_post_capture_" + key
        capture("03i-sync-760-" + key)
        # The prior failure report only said "AssertionError" after this
        # screenshot; record which of the strict no-side-effect gates failed.
        side_effects = {
            "source_sync_calls": len(f["source_sync_calls"]),
            "prepare_calls": len(f["prepare_calls"]),
            "materialize_calls": len(f["materialize_calls"]),
        }
        report["sync_760_post_capture"] = side_effects
        assert not any(side_effects.values()), (
            "P1_1889: sync layout must not trigger source or acquisition work",
            side_effects,
        )
        if not large_font:
            gtk_phase_2027("sync_font_narrow")
            Gtk.Settings.get_default().set_property("gtk-font-name", "Sans 12")
            later(350, lambda: sync_narrow_allocations(True))
            return
        gtk_phase_2027("sync_restored")
        Gtk.Settings.get_default().set_property("gtk-font-name", f["sync_original_font"])
        f["sync_long_name_row"].set_title(f["sync_original_title"])
        w.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
        later(350, begin_numilog_inventory_acceptance)

    def begin_numilog_inventory_acceptance():
        gtk_phase_2027("numilog_consent")
        w = window[0]
        f = loan_sync_fixture
        scroll = f["sync_scroll"]
        report["stage"] = "P0_1905_numilog_inventory_preflight"
        rows = [node for node in walk(w._sync_sources_box) if isinstance(node, Adw.ActionRow)]
        # Exercise Numilog's explicit consent with the real GTK dialog before
        # continuing the existing Paris/Kobo acceptance path. Broker data is
        # synthetic; policy writes cannot execute inventory or acquisition.
        inventory_rows = [
            row for row in rows
            if row.get_title() == "Actualisation automatique des notices Numilog"
        ]
        preflight = {
            "matching_rows": len(inventory_rows),
            "window_size": [w.get_width(), w.get_height()],
            "scroll_size": [scroll.get_width(), scroll.get_height()],
            "scroll_mapped": bool(scroll.get_mapped()),
            "scroll_value": round(scroll.get_vadjustment().get_value(), 2),
            "policy_write_count": len(f["inventory_policy_writes"]),
        }
        report["numilog_inventory_preflight"] = preflight
        assert len(inventory_rows) == 1, (
            "P0_1905: expected one Numilog inventory row", preflight,
        )
        inventory_row = inventory_rows[0]
        preflight["manual_subtitle"] = "Mode manuel" in (inventory_row.get_subtitle() or "")
        assert preflight["manual_subtitle"], (
            "P0_1905: Numilog consent row no longer starts in manual mode", preflight,
        )
        buttons = [
            node for node in walk(sync_action_row(inventory_row))
            if isinstance(node, Gtk.Button)
            and getattr(node, "_cifereca_action_label", None) == "Planifier les notices…"
        ]
        preflight["matching_buttons"] = len(buttons)
        assert len(buttons) == 1, (
            "P0_1905: expected one Numilog planning button", preflight,
        )
        button = buttons[0]
        preflight["button_sensitive"] = bool(button.get_sensitive())
        preflight["button_mapped"] = bool(button.get_mapped())
        assert button.get_sensitive() and not f["inventory_policy_writes"], (
            "P0_1905: planning disabled or consent written before explicit click", preflight,
        )
        ok, bounds = button.compute_bounds(scroll)
        preflight["bounds_available"] = bool(ok)
        if ok:
            preflight["button_bounds"] = [
                round(bounds.get_x(), 2), round(bounds.get_y(), 2),
                round(bounds.get_width(), 2), round(bounds.get_height(), 2),
            ]
        assert ok, ("P0_1905: planning button has no scroll-relative bounds", preflight)
        adjustment = scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_value() + bounds.get_y() - 100)
        later(250, numilog_inventory_schedule_click)

    def numilog_inventory_schedule_click():
        w = window[0]
        f = loan_sync_fixture
        report["stage"] = "P0_1905_numilog_inventory_click"
        row = next(row for row in walk(w._sync_sources_box)
                   if isinstance(row, Adw.ActionRow)
                   and row.get_title() == "Actualisation automatique des notices Numilog")
        button = next(node for node in walk(sync_action_row(row))
                      if isinstance(node, Gtk.Button) and getattr(node, "_cifereca_action_label", None) == "Planifier les notices…")
        ok, bounds = button.compute_bounds(f["sync_scroll"])
        click_geometry = {
            "bounds_available": bool(ok),
            "scroll_height": f["sync_scroll"].get_height(),
            "scroll_value": round(f["sync_scroll"].get_vadjustment().get_value(), 2),
            "button_mapped": bool(button.get_mapped()),
        }
        if ok:
            click_geometry["button_bounds"] = [
                round(bounds.get_x(), 2), round(bounds.get_y(), 2),
                round(bounds.get_width(), 2), round(bounds.get_height(), 2),
            ]
        report["numilog_inventory_click_geometry"] = click_geometry
        # GTK can reflow the FlowBox *after* the scrollbar moves: the 760 px
        # preflight measured y=5395 but a later allocation can move the
        # button above the viewport (observed y=-593 on CI #2032).
        # Correct only the viewport and wait for one settled frame. Never
        # click an invisible widget, skip a validation, or retry a failed run.
        viewport_height = f["sync_scroll"].get_height()
        if ok and (
            bounds.get_y() < 0
            or bounds.get_y() + bounds.get_height() > viewport_height
        ):
            attempts = f.get("numilog_inventory_scroll_corrections", 0) + 1
            f["numilog_inventory_scroll_corrections"] = attempts
            click_geometry["correction_attempt"] = attempts
            report.setdefault("numilog_inventory_scroll_history", []).append(
                dict(click_geometry)
            )
            assert attempts <= 8, (
                "P0_1905: Numilog planning button never settled in the viewport",
                report["numilog_inventory_scroll_history"],
            )
            adjustment = f["sync_scroll"].get_vadjustment()
            adjustment.set_value(adjustment.get_value() + bounds.get_y() - 100)
            later(160, numilog_inventory_schedule_click)
            return
        assert ok and bounds.get_y() >= 0, (
            "P0_1905: planning button lacks visible scroll-relative bounds", click_geometry,
        )
        assert bounds.get_y() + bounds.get_height() <= viewport_height, (
            "P0_1905: planning button outside viewport after scroll", click_geometry,
        )
        # Closed diagnostic phases only: distinguish geometry, pointer dispatch
        # and dialog realization without exporting GTK text or coordinates.
        report["stage"] = "P0_1905_numilog_inventory_pointer_dispatch"
        report["pointer_actions"].append(device[0].click_widget(button))
        report["stage"] = "P0_1905_numilog_inventory_dialog_wait"
        later(250, numilog_inventory_schedule_dialog)

    def numilog_inventory_schedule_dialog():
        report["stage"] = "P0_1905_numilog_inventory_dialog_preflight"
        f = loan_sync_fixture
        dialogs = [node for node in Gtk.Window.list_toplevels()
                   if isinstance(node, Adw.MessageDialog) and node.get_visible()
                   and node.get_heading() == "Planifier les notices Numilog ?"]
        assert len(dialogs) == 1
        dialog = dialogs[0]
        assert "sans téléchargement" in dialog.get_body()
        assert not f["inventory_policy_writes"] and not f["prepare_calls"]
        assert not f["materialize_calls"] and not f["source_sync_calls"]
        button = next(node for node in walk(dialog) if isinstance(node, Gtk.Button)
                      and str(node.get_label()).replace("_", "") == "Périodique")
        report["stage"] = "P0_1905_numilog_inventory_dialog_ready"
        capture("03i1-numilog-inventory-consent")
        report["pointer_actions"].append(device[0].click_widget(button))
        later(300, numilog_inventory_schedule_revoke)

    def numilog_inventory_schedule_revoke():
        w = window[0]
        f = loan_sync_fixture
        assert f["inventory_policy_writes"] == [("p0-numilog-inventory", "periodic")]
        row = next(row for row in walk(w._sync_sources_box)
                   if isinstance(row, Adw.ActionRow)
                   and row.get_title() == "Actualisation automatique des notices Numilog")
        assert "périodique autorisée" in row.get_subtitle()
        button = next(node for node in walk(sync_action_row(row)) if isinstance(node, Gtk.Button)
                      and getattr(node, "_cifereca_action_label", None) == "Désactiver les actualisations")
        # A refresh may reset scroll allocation; bring the new button into view.
        scroll = f["sync_scroll"]
        ok, bounds = button.compute_bounds(scroll)
        assert ok
        adjustment = scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_value() + bounds.get_y() - 100)
        f["inventory_revoke_button"] = button
        later(200, numilog_inventory_revoke_click)

    def numilog_inventory_revoke_click():
        f = loan_sync_fixture
        button = f["inventory_revoke_button"]
        scroll = f["sync_scroll"]
        ok, bounds = button.compute_bounds(scroll)
        assert ok, "Numilog revocation action has no viewport bounds"
        if bounds.get_y() < 0 or bounds.get_y() + bounds.get_height() > scroll.get_height():
            # Page regeneration resets the adjustment before GTK allocates
            # the replacement rows. Scroll against the *current* allocation,
            # then recheck on a later frame before sending a physical click.
            attempts = f.get("inventory_revoke_scroll_attempts", 0) + 1
            f["inventory_revoke_scroll_attempts"] = attempts
            assert attempts <= 6, "Numilog revocation remained outside the viewport"
            adjustment = scroll.get_vadjustment()
            adjustment.set_value(adjustment.get_value() + bounds.get_y() - 100)
            later(180, numilog_inventory_revoke_click)
            return
        assert button.get_mapped() and button.get_sensitive()
        report["pointer_actions"].append(device[0].click_widget(button))
        later(250, numilog_inventory_schedule_complete)

    def numilog_inventory_schedule_complete():
        f = loan_sync_fixture
        assert f["inventory_policy_writes"] == [
            ("p0-numilog-inventory", "periodic"), ("p0-numilog-inventory", "manual"),
        ]
        assert not f["prepare_calls"] and not f["materialize_calls"]
        assert not f["source_sync_calls"] and not f["mutations"]
        report["checks"]["P0_1905_Numilog_XTEST_inventory_consent_and_revocation_without_acquisition"] = True
        assert not f["numilog_preview_calls"], "Rendering/consent must not read local evidence"
        numilog_preview_click(False)

    def numilog_preview_click(verify):
        f = loan_sync_fixture
        row = next(row for row in walk(window[0]._sync_sources_box)
                   if isinstance(row, Adw.ActionRow)
                   and row.get_title() == "Aperçu local des achats Numilog")
        label = "Vérifier les fichiers…" if verify else "Aperçu local…"
        button = next(node for node in walk(sync_action_row(row)) if isinstance(node, Gtk.Button)
                      and getattr(node, "_cifereca_action_label", None) == label)
        scroll = f["sync_scroll"]
        ok, bounds = button.compute_bounds(scroll)
        assert ok
        if bounds.get_y() < 0 or bounds.get_y() + bounds.get_height() > scroll.get_height():
            key = "numilog_preview_scroll_" + str(verify)
            f[key] = f.get(key, 0) + 1
            assert f[key] <= 6
            adjustment = scroll.get_vadjustment()
            adjustment.set_value(adjustment.get_value() + bounds.get_y() - 100)
            later(180, lambda: numilog_preview_click(verify))
            return
        assert button.get_mapped() and button.get_sensitive()
        assert row.get_width() <= scroll.get_width()
        if verify:
            first = next(node for node in walk(sync_action_row(row)) if isinstance(node, Gtk.Button)
                         and getattr(node, "_cifereca_action_label", None) == "Aperçu local…")
            first.grab_focus()
            def tab_to_verify(attempt=0):
                focus = window[0].get_focus()
                current = focus
                while current is not None and current is not button:
                    current = current.get_parent()
                if current is button:
                    device[0].key("space", True); device[0].key("space", False)
                    report["checks"]["P1_1889_sync_Tab_then_space_preserves_action"] = True
                    later(300, lambda: numilog_preview_complete(verify, row))
                    return
                assert attempt < 6, "Verification action unreachable by Tab"
                device[0].key("Tab", True); device[0].key("Tab", False)
                later(80, lambda: tab_to_verify(attempt + 1))
            tab_to_verify()
            return
        report["pointer_actions"].append(device[0].click_widget(button))
        later(300, lambda: numilog_preview_complete(verify, row))

    def numilog_preview_complete(verify, row):
        f = loan_sync_fixture
        expected = [("p0-numilog-inventory", False)]
        if verify:
            expected.append(("p0-numilog-inventory", True))
        assert f["numilog_preview_calls"] == expected
        subtitle = row.get_subtitle()
        assert "Achats connus : 3" in subtitle and "aucun téléchargement autorisé" in subtitle
        assert "PRIVATE" not in subtitle
        assert ("Vérification SHA demandée" if verify else "Sans lecture ni vérification SHA") in subtitle
        assert not f["prepare_calls"] and not f["materialize_calls"]
        assert not f["source_sync_calls"] and not f["mutations"]
        capture("03i3-numilog-sha-preview" if verify else "03i2-numilog-offline-preview")
        if not verify:
            numilog_preview_click(True)
            return
        report["checks"]["P0_1905_Numilog_XTEST_preview_then_explicit_SHA_without_acquisition"] = True
        # Accounts precede public-only catalogues (#2141), so Paris is
        # no longer guaranteed to be at the scroll bottom. Find the real
        # target inside sync_homonyms_click before dispatching XTEST.
        later(200, sync_homonyms_click)

    def sync_homonyms_click():
        w = window[0]
        f = loan_sync_fixture
        row = next(row for row in walk(w._sync_sources_box)
                   if isinstance(row, Adw.ActionRow)
                   and row.get_title() == "Paris homonyme · paris · connexion 2/2")
        button = next(node for node in walk(sync_action_row(row)) if isinstance(node, Gtk.Button)
                      and node.get_tooltip_text() == "Synchroniser Paris homonyme")
        assert button.get_sensitive() and button.get_mapped()
        scroll = f["sync_scroll"]
        assert isinstance(scroll, Gtk.ScrolledWindow)
        ok, bounds = button.compute_bounds(scroll)
        assert ok, "Paris targeted sync button has no scroll bounds"
        top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
        height = scroll.get_height()
        assert height > 100 and bottom > top
        if top < 4 or bottom > height - 4:
            attempt = f.get("paris_scroll_attempts", 0) + 1
            assert attempt <= 5, ("Paris target remained outside viewport",
                                  top, bottom, height)
            f["paris_scroll_attempts"] = attempt
            adjustment = scroll.get_vadjustment()
            target = adjustment.get_value() + top - min(80, height / 4)
            maximum = max(adjustment.get_lower(),
                          adjustment.get_upper() - adjustment.get_page_size())
            adjustment.set_value(max(adjustment.get_lower(), min(target, maximum)))
            later(180, sync_homonyms_click)
            return
        assert top >= 0 and bottom <= height
        ok, window_bounds = button.compute_bounds(w)
        assert ok and window_bounds.get_y() >= 0
        assert window_bounds.get_y() + window_bounds.get_height() <= w.get_height()
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

    def kobo_offline_audit_row():
        """Resolve the audited account before its repeated detail title.

        #2141 sorts enabled/synchronisable Kobo ahead of the offline account.
        Selecting the first audit detail globally would inspect another
        account's unchanged subtitle and produce a false negative.
        """
        w = window[0]
        groups = [
            group for group in walk(w._sync_sources_box)
            if isinstance(group, Adw.PreferencesGroup)
            and any(
                isinstance(item, Adw.ActionRow)
                and item.get_title() == "Kobo audit hors ligne"
                for item in walk(group)
            )
        ]
        assert len(groups) == 1, "Offline Kobo audit account is not unique"
        matches = [
            item for item in walk(groups[0])
            if isinstance(item, Adw.ActionRow)
            and item.get_title() == "Vérification des fichiers et rapprochements"
        ]
        assert len(matches) == 1, "Offline Kobo audit detail is not unique"
        return groups[0], matches[0]

    def loan_sync_audit_click():
        w = window[0]
        f = loan_sync_fixture
        rows = [node for node in walk(w._sync_sources_box) if isinstance(node, Adw.ActionRow)]
        kobo = next(row for row in rows if row.get_title() == "Kobo audit hors ligne")
        assert "Connexion : Déconnectée" in kobo.get_subtitle()
        # #2141: secondary actions are now below the account's explicitly
        # opened tools expander, not in the primary ActionRow suffix.
        # Keep target account and repeated audit-detail title scoped.
        account_group, _ = kobo_offline_audit_row()
        assert any(node is kobo for node in walk(account_group))
        audits = [button for button in walk(account_group)
                  if isinstance(button, Gtk.Button)
                  and button.get_tooltip_text() == (
                      "Audit SHA local de ce compte (lecture seule, peut être long)"
                  )]
        assert len(audits) == 1, "Kobo audit action must remain account-scoped"
        audit = audits[0]
        assert audit.get_sensitive() and audit.get_mapped()
        assert not f["audit_calls"] and not f["mutations"]
        # GTK mapping is NOT on-screen visibility. An extra confirmation row
        # (P0 #1347) pushed this button to y=2036 on a 900px Xvfb screen;
        # click_widget then clicked outside the display without invoking the
        # audit. Scroll the *real* Gtk.ScrolledWindow until the action can be
        # physically reached. Do not weaken the audit-result assertions.
        scroll = f["sync_scroll"]
        assert isinstance(scroll, Gtk.ScrolledWindow)
        ok, bounds = audit.compute_bounds(scroll)
        assert ok, "Kobo audit action has no scroll-viewport bounds"
        top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
        height = scroll.get_height()
        assert height > 100 and bottom > top, (top, bottom, height)
        if top < 4 or bottom > height - 4:
            attempt = f.get("audit_scroll_attempts", 0) + 1
            assert attempt <= 5, (
                "Kobo audit button remained outside visible viewport",
                top, bottom, height,
            )
            f["audit_scroll_attempts"] = attempt
            adjustment = scroll.get_vadjustment()
            target = adjustment.get_value() + top - min(80, height / 4)
            maximum = max(adjustment.get_lower(),
                          adjustment.get_upper() - adjustment.get_page_size())
            adjustment.set_value(max(adjustment.get_lower(), min(target, maximum)))
            later(180, loan_sync_audit_click)
            return
        ok, window_bounds = audit.compute_bounds(w)
        assert ok, "Kobo audit action has no MainWindow bounds"
        assert 0 <= window_bounds.get_y() and (
            window_bounds.get_y() + window_bounds.get_height() <= w.get_height()
        ), "Kobo audit action is outside the displayed window"
        # GTK can finish a scroll allocation after compute_bounds reports an
        # on-screen rectangle. The XTEST event is delivered after this GTK
        # callback returns; sending it during reflow can hit another widget.
        # Require a stable hit target on two separate main-loop iterations.
        point_x = window_bounds.get_x() + window_bounds.get_width() / 2
        point_y = window_bounds.get_y() + window_bounds.get_height() / 2
        picked = w.pick(point_x, point_y, Gtk.PickFlags.DEFAULT)
        while picked is not None and picked is not audit:
            picked = picked.get_parent()
        geometry = (
            round(window_bounds.get_x()), round(window_bounds.get_y()),
            round(window_bounds.get_width()), round(window_bounds.get_height()),
            round(scroll.get_vadjustment().get_value()),
        )
        if picked is not audit or f.get("audit_stable_geometry") != geometry:
            attempt = f.get("audit_stable_checks", 0) + 1
            assert attempt <= 8, "Kobo audit physical hit target did not settle"
            f["audit_stable_checks"] = attempt
            f["audit_stable_geometry"] = geometry
            later(130, loan_sync_audit_click)
            return
        report["checks"]["P0_kobo_audit_XTEST_target_inside_viewport"] = True
        report["checks"]["P0_kobo_audit_offline_button_no_implicit_scan"] = True
        # A physical XTEST action is the sole trigger; rendering the row does
        # not authorize or start any inspection.
        # Observe the real GTK "clicked" signal without invoking the action
        # programmatically. A missed XTEST hit must remain a test failure.
        f["audit_click_signal_seen"] = False
        audit.connect(
            "clicked",
            lambda _button: f.__setitem__("audit_click_signal_seen", True),
        )
        report["stage"] = "P0_1347_kobo_audit_pointer_dispatch"
        # A GTK hit-test on the window does not prove that X11/GDK actually
        # delivered pointer motion to the button. Place the *physical* pointer
        # first, return to GTK's main loop, and accept only a real GTK motion
        # or enter signal on this exact button before pressing. No synthetic
        # GTK activation or second button click is allowed.
        if f.get("audit_motion_button") is not audit:
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: f.__setitem__("audit_motion_seen", True))
            motion.connect("motion", lambda *_: f.__setitem__("audit_motion_seen", True))
            audit.add_controller(motion)
            f["audit_motion_controller"] = motion
            f["audit_motion_button"] = audit
        f["audit_motion_seen"] = False
        screen_x, screen_y = device[0].window_point(w, point_x, point_y)
        device[0].move(screen_x, screen_y)
        f["audit_motion_target"] = (audit, geometry, point_x, point_y, screen_x, screen_y)
        later(130, loan_sync_audit_after_motion)

    def loan_sync_audit_after_motion():
        w = window[0]
        f = loan_sync_fixture
        audit, geometry, px, py, sx, sy = f["audit_motion_target"]
        ok, rect = audit.compute_bounds(w)
        current = (
            round(rect.get_x()), round(rect.get_y()),
            round(rect.get_width()), round(rect.get_height()),
            round(f["sync_scroll"].get_vadjustment().get_value()),
        ) if ok else None
        picked = w.pick(px, py, Gtk.PickFlags.DEFAULT)
        while picked is not None and picked is not audit:
            picked = picked.get_parent()
        current_screen = device[0].window_point(w, px, py)
        if (not f["audit_motion_seen"] or current != geometry
                or picked is not audit or current_screen != (sx, sy)
                or not audit.get_mapped() or not audit.get_sensitive()):
            # We have not clicked. Retry *pointer placement* only, with a
            # closed bound; never misreport a missed physical click as success.
            attempts = f.get("audit_motion_attempts", 0) + 1
            f["audit_motion_attempts"] = attempts
            assert attempts <= 5, "Kobo audit XTEST pointer could not hit GTK button"
            f["audit_stable_geometry"] = None
            f["audit_stable_checks"] = 0
            later(130, loan_sync_audit_click)
            return
        # Only local object identity/generation snapshots. Publication exports
        # four closed booleans, never IDs, account details or GTK contents.
        f["audit_row_at_click"] = kobo_offline_audit_row()[1]
        f["audit_preview_generation_at_click"] = getattr(w, "_sync_preview_generation", None)
        f["audit_identity_generation_at_click"] = getattr(w, "_identity_context_generation", None)
        f["audit_page_token_at_click"] = getattr(w, "_sync_page_token", None)
        # Exactly one genuine XTEST press/release on a proven GTK hover target.
        device[0].click(sx, sy)
        report["pointer_actions"].append({
            "widget": audit.get_name(), "x": sx, "y": sy,
            "source": "XTEST pointer/button events (verified GTK pointer motion)",
        })
        report["stage"] = "P0_1347_kobo_audit_result_wait"
        f["audit_deadline"] = monotonic() + 6
        later(100, loan_sync_audit_result)

    def loan_sync_audit_result():
        w = window[0]
        f = loan_sync_fixture
        audit_row = kobo_offline_audit_row()[1]
        subtitle = audit_row.get_subtitle()
        if "Fichiers locaux vérifiés par SHA : 3" not in subtitle:
            now = monotonic()
            if now >= f["audit_deadline"]:
                # Closed fixture facts only. Never publish account/source IDs,
                # GTK text, coordinates, worker exceptions, or raw traces.
                report["kobo_audit_observation"] = {
                    "clicked_signal": f["audit_click_signal_seen"] is True,
                    "broker_calls": min(len(f["audit_calls"]), 2),
                    "row_recreated": audit_row is not f.get("audit_row_at_click"),
                    "preview_generation_changed": (
                        getattr(w, "_sync_preview_generation", None)
                        != f.get("audit_preview_generation_at_click")
                    ),
                    "identity_generation_changed": (
                        getattr(w, "_identity_context_generation", None)
                        != f.get("audit_identity_generation_at_click")
                    ),
                    "page_rebuilt": (
                        getattr(w, "_sync_page_token", None)
                        is not f.get("audit_page_token_at_click")
                    ),
                    "subtitle_phase": (
                        "in_progress" if subtitle == "Inspection locale en cours (lecture seule)…"
                        else "initial" if subtitle == (
                            "Non consulté · aucun téléchargement, déplacement ou suppression"
                        ) else "other"
                    ),
                }
            assert now < f["audit_deadline"], (
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
        w.navigate("library")
        await_route("library", kobo_library_operations)

    def kobo_library_operations():
        w = window[0]
        menu = next(node for node in walk(w) if isinstance(node, Gtk.MenuButton)
                    and node.get_label() == "Opérations…")
        loan_sync_fixture["operations_menu"] = menu
        report["pointer_actions"].append(device[0].click_widget(menu))
        later(180, kobo_library_sync_navigation)

    def kobo_library_sync_navigation():
        f = loan_sync_fixture
        popover = f["operations_menu"].get_popover()
        assert popover.get_mapped()
        button = next(node for node in walk(popover) if isinstance(node, Gtk.Button)
                      and node.get_label() == "Gérer la synchronisation Kobo…")
        assert not f["prepare_calls"] and not f["materialize_calls"]
        report["pointer_actions"].append(device[0].click_widget(button))
        await_route("sync", kobo_download_begin)

    def kobo_download_begin():
        f = loan_sync_fixture
        w = window[0]
        assert not f["prepare_calls"] and not f["materialize_calls"]
        report["checks"]["P0_1347_library_menu_XTEST_navigates_without_acquisition"] = True
        groups = [node for node in walk(w._sync_sources_box)
                  if isinstance(node, Adw.PreferencesGroup)]
        groups = [group for group in groups if any(
            isinstance(row, Adw.ActionRow) and row.get_title() == "Kobo ciblé 2"
            for row in walk(group))]
        assert len(groups) == 1
        f["download_group"] = groups[0]
        f["download_row"] = next(row for row in walk(groups[0])
            if isinstance(row, Adw.ActionRow)
            and row.get_title() == "Téléchargement local · confirmation obligatoire")
        assert "Non préparé" in f["download_row"].get_subtitle()
        f["download_button"] = next(node for node in walk(groups[0])
            if isinstance(node, Gtk.Button)
            and getattr(node, "_cifereca_action_label", node.get_label()) == "Télécharger les livres manquants…")
        f["stop_button"] = next(node for node in walk(groups[0])
            if isinstance(node, Gtk.Button)
            and getattr(node, "_cifereca_action_label", node.get_label()) == "Annuler le téléchargement")
        report["checks"]["P0_1347_library_menu_XTEST_navigates_without_acquisition"] = True
        f["download_phase"] = "cancel-dialog"
        kobo_download_prepare_click()

    def kobo_click_visible(button, continuation):
        # Real allocation, scrolling and physical pointer; mapping alone is
        # insufficient for the long source list at either viewport width.
        w = window[0]
        scroll = loan_sync_fixture["sync_scroll"]
        ok, bounds = button.compute_bounds(scroll)
        assert ok
        top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
        if top < 4 or bottom > scroll.get_height() - 4:
            attempts = loan_sync_fixture.get("download_scroll_attempts", 0) + 1
            loan_sync_fixture["download_scroll_attempts"] = attempts
            assert attempts <= 6, "Action Kobo toujours hors du viewport"
            adjustment = scroll.get_vadjustment()
            maximum = max(adjustment.get_lower(),
                          adjustment.get_upper() - adjustment.get_page_size())
            adjustment.set_value(max(adjustment.get_lower(), min(
                adjustment.get_value() + top - 60, maximum)))
            later(180, lambda: kobo_click_visible(button, continuation))
            return
        loan_sync_fixture["download_scroll_attempts"] = 0
        ok, bounds = button.compute_bounds(w)
        assert ok and 0 <= bounds.get_y() and (
            bounds.get_y() + bounds.get_height() <= w.get_height())
        assert button.get_sensitive() and button.get_mapped()
        report["pointer_actions"].append(device[0].click_widget(button))
        later(120, continuation)

    def kobo_download_prepare_click():
        f = loan_sync_fixture
        f["download_deadline"] = monotonic() + 6
        kobo_click_visible(f["download_button"], kobo_download_dialog)

    def kobo_download_dialog():
        f = loan_sync_fixture
        dialogs = [node for node in Gtk.Window.list_toplevels()
                   if isinstance(node, Adw.MessageDialog) and node.get_visible()
                   and node.get_heading() == "Confirmer les téléchargements Kobo ?"]
        if not dialogs:
            assert monotonic() < f["download_deadline"], "Confirmation Kobo absente"
            later(100, kobo_download_dialog)
            return
        assert len(dialogs) == 1 and dialogs[0].get_mapped()
        dialog = dialogs[0]
        f["dialog"] = dialog
        body = dialog.get_body()
        assert "Kobo ciblé 2" in body and "déjà locaux : 1" in body
        assert "manquants possibles : 2" in body
        assert "fictitious-target" not in body
        phase = f["download_phase"]
        if phase == "cancel-dialog":
            assert not f["materialize_calls"]
            label = "Annuler"
            next_step = kobo_download_dialog_cancelled
        else:
            label = "Télécharger pour ce compte"
            next_step = kobo_download_running if phase == "cancel-running" else kobo_download_finished
        button = next(node for node in walk(dialog) if isinstance(node, Gtk.Button)
                      and str(node.get_label()).replace("_", "") == label)
        # GTK can report a modal as mapped before its button has real X11
        # coordinates. A (-5, -5) XTEST click was observed before an unintended
        # Kobo materialization in two separate CI runs. Never send that click.
        try:
            target = device[0].widget_point(button) if button.get_mapped() else (-1, -1)
        except RuntimeError:
            target = (-1, -1)
        if target[0] < 0 or target[1] < 0:
            assert monotonic() < f["download_deadline"], (
                "Kobo confirmation button has no on-screen XTEST coordinates"
            )
            later(80, kobo_download_dialog)
            return
        capture("03k-kobo-explicit-source-confirmation-" + phase)
        report["pointer_actions"].append(device[0].click_widget(button))
        f["download_deadline"] = monotonic() + 6
        later(120, next_step)

    def kobo_download_dialog_cancelled():
        f = loan_sync_fixture
        # Cancellation must never reach materialization, including while GTK
        # animates the modal closure. Do not mistake a delayed redraw for a
        # failed cancellation; retain a bounded hard failure if it stays open.
        assert not f["materialize_calls"], "Kobo cancellation triggered acquisition"
        if f["dialog"].get_visible():
            assert monotonic() < f["download_deadline"], (
                "Kobo confirmation still visible after cancel deadline"
            )
            later(80, kobo_download_dialog_cancelled)
            return
        report["checks"]["P0_1347_Kobo_XTEST_prepare_cancel_zero_acquisition"] = True
        f["download_phase"] = "cancel-running"
        kobo_download_prepare_click()

    def kobo_download_running():
        f = loan_sync_fixture
        if not f["download_started"].is_set():
            assert monotonic() < f["download_deadline"], "Worker Kobo absent"
            later(100, kobo_download_running)
            return
        assert f["materialize_calls"] == ["p0-kobo-target-2"]
        f["cancel_future"] = f.get("latest_task_future")
        assert f["cancel_future"] is not None
        kobo_click_visible(f["stop_button"], kobo_download_release)

    def kobo_download_release():
        f = loan_sync_fixture
        assert "Annulation demandée" in f["download_row"].get_subtitle()
        # The prior deadline covered the confirmation dialog and scrolling
        # to the real stop button. Start a distinct bounded deadline only
        # after XTEST cancellation has reached GTK and the worker is released.
        # Otherwise a slow 1600px layout can time out before GTK processes
        # the worker's terminal result, without actually failing acquisition.
        f["download_release"].set()
        # A worker has its own strict 10-second wait for the cancellation
        # event. Allow that bound plus GTK delivery on loaded CI runners;
        # never declare cancellation without the actual terminal receipt.
        f["download_deadline"] = monotonic() + 12
        later(120, kobo_download_cancelled)

    def kobo_download_cancelled():
        f = loan_sync_fixture
        subtitle = f["download_row"].get_subtitle()
        if not subtitle.startswith("Annulé ·"):
            # Explain whether the release event failed, the fake worker did
            # not return, or TaskRunner/GTK has not rendered completion.
            # The assertion is intentionally retained and remains fatal.
            future = f.get("cancel_future")
            evidence = {
                "future_running": bool(future is not None and future.running()),
                "future_done": bool(future is not None and future.done()),
                "release_set": f["download_release"].is_set(),
                "worker_entered": f["cancel_worker_entered"].is_set(),
                "worker_woke": f["cancel_worker_woke"].is_set(),
                "worker_returning": f["cancel_worker_returning"].is_set(),
                "task_active": bool(getattr(window[0].runner, "_count", 0)),
            }
            assert monotonic() < f["download_deadline"], (
                f"Résultat annulé absent · {evidence}"
            )
            later(100, kobo_download_cancelled)
            return
        assert "1 fichier(s) acquis durablement" in subtitle
        assert "Relancer une préparation" in subtitle
        assert not f["stop_button"].get_sensitive()
        report["checks"]["P0_1347_Kobo_XTEST_cancel_keeps_partial_count"] = True
        capture("03l-kobo-cancelled-result")
        f["download_phase"] = "retry"
        kobo_download_prepare_click()

    def kobo_download_finished():
        f = loan_sync_fixture
        subtitle = f["download_row"].get_subtitle()
        if not subtitle.startswith("Téléchargement terminé ·"):
            assert monotonic() < f["download_deadline"], "Résultat terminé absent"
            later(100, kobo_download_finished)
            return
        assert "1 fichier(s) acquis durablement" in subtitle
        assert f["prepare_calls"] == ["p0-kobo-target-2"] * 3
        assert f["materialize_calls"] == ["p0-kobo-target-2"] * 2
        assert not f["mutations"] and not f["stop_button"].get_sensitive()
        report["checks"]["P0_1347_Kobo_XTEST_three_accounts_only_second_confirmed"] = True
        report["checks"]["P0_1347_Kobo_XTEST_retry_requires_new_preparation"] = True
        report["kobo_confirmation_scope"] = (
            "real GTK/TaskRunner/XTEST; three synthetic accounts and observed broker boundary; "
            "no live provider, file or DRM acquisition claimed")
        capture("03m-kobo-single-account-completed-result")
        begin_source_sync_consent_acceptance()

    def begin_source_sync_consent_acceptance():
        # Exercise the modern broker path in the same real GTK/XTEST shell.
        # The provider is deliberately unknown to the CoreUI; only DTOs and
        # local source-consent commands are supplied, no provider acquisition.
        from cifereca.plugin_api import SyncSourceRef
        w = window[0]
        f = loan_sync_fixture
        broker = w.plugin_manager.broker("sync")
        source = SyncSourceRef(
            source_id="p0-canonical-unknown", identity_id="camille",
            provider_id="fixture-unknown", account_id="synthetic-account",
            label="Source générique fictive", sync_capabilities=("remote_inventory",),
        )
        state = dict(supported=True, configured=False, enabled=False,
                     revision=None, valid=True)
        f["canonical_writes"] = []
        def write(sid, *, enabled, expected_revision):
            assert sid == source.source_id and expected_revision == state["revision"]
            f["canonical_writes"].append((sid, enabled, expected_revision))
            state.update(configured=True, enabled=enabled,
                         revision="source-epoch-1" if enabled else "source-epoch-2")
            return dict(state)
        for name, value in (
            ("sources", lambda identity_id: (source,) if identity_id == "camille" else ()),
            ("source_sync_consent", lambda sid: dict(state)),
            ("set_source_sync_consent", write),
        ):
            f["overrides"].append((broker, name, name in vars(broker), vars(broker).get(name)))
            setattr(broker, name, value)
        w._refresh_sync_page()
        scroll = w._sync_sources_box.get_parent()
        while not isinstance(scroll, Gtk.ScrolledWindow):
            scroll = scroll.get_parent()
        scroll.get_vadjustment().set_value(0)
        later(200, source_sync_consent_initial_choice)

    def source_sync_consent_initial_choice():
        w = window[0]
        f = loan_sync_fixture
        assert not f["canonical_writes"], "Rendering must not expand an old grant"
        row = next(node for node in walk(w._sync_sources_box)
                   if isinstance(node, Adw.ActionRow)
                   and node.get_title() == "Téléchargement automatique de ce compte")
        on = next(node for node in walk(sync_action_row(row))
                  if isinstance(node, Gtk.Button)
                  and getattr(node, "_cifereca_action_label", node.get_label()) == "Activer")
        assert on.get_mapped() and on.get_sensitive()
        report["pointer_actions"].append(device[0].click_widget(on))
        later(250, source_sync_consent_rendered)

    def source_sync_consent_rendered():
        w = window[0]
        f = loan_sync_fixture
        rows = [node for node in walk(w._sync_sources_box) if isinstance(node, Adw.ActionRow)]
        header = next(row for row in rows if row.get_title() == "Source générique fictive")
        assert "fichiers disponibles" in header.get_subtitle()
        assert not any(isinstance(node, Gtk.Expander)
                       and node.get_label() == "Outils et planification"
                       for node in walk(w._sync_sources_box))
        assert not any("Acquisition canonique" in row.get_title() for row in rows)
        consent = next(row for row in rows if row.get_title() == "Téléchargement automatique de ce compte")
        toggle, = [node for node in walk(consent) if isinstance(node, Gtk.Switch)]
        assert toggle.get_active() and toggle.get_mapped()
        assert f["canonical_writes"] == [("p0-canonical-unknown", True, None)]
        f["canonical_old_toggle"] = toggle
        capture("03n-canonical-source-consent-before")
        report["pointer_actions"].append(device[0].click_widget(toggle))
        later(250, source_sync_consent_revoked)

    def source_sync_consent_revoked():
        w = window[0]
        f = loan_sync_fixture
        assert f["canonical_writes"] == [
            ("p0-canonical-unknown", True, None),
            ("p0-canonical-unknown", False, "source-epoch-1"),
        ]
        row = next(node for node in walk(w._sync_sources_box)
                   if isinstance(node, Adw.ActionRow)
                   and node.get_title() == "Téléchargement automatique de ce compte")
        toggle, = [node for node in walk(row) if isinstance(node, Gtk.Switch)]
        assert not toggle.get_active() and "Désactivé" in row.get_subtitle()
        f["canonical_old_toggle"].set_active(True)
        assert len(f["canonical_writes"]) == 2, "Replaced GTK handler must remain inert"
        report["checks"]["P0_3416_unknown_provider_generic_consent_XTEST_OFF"] = True
        report["checks"]["P0_3416_generic_consent_render_no_write_and_stale_widget_inert"] = True
        capture("03o-canonical-source-consent-off")
        cleanup_loan_sync()
        later(100, continue_baseline_settings)

    def cleanup_loan_sync():
        f = loan_sync_fixture
        if not f.get("overrides"):
            return
        w = window[0]
        if f.get("download_release") is not None:
            f["download_release"].set()
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
                row.get_title() == "Ancien réglage global Kobo (compatibilité)"
                and "ne lance plus de téléchargements" in row.get_subtitle()
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
        # Earlier baseline identity changes can report the absent synthetic
        # local provider. Acknowledge that exact offline-fixture notification
        # physically; never ignore an arbitrary product error or blocked click.
        dialogs = [node for node in Gtk.Window.list_toplevels()
                   if isinstance(node, Adw.MessageDialog) and node.get_visible()]
        if dialogs:
            assert all(
                node.get_body() == "Traitement indisponible"
                and getattr(node, "_cifereca_attested_offline_fixture", False) is True
                for node in dialogs
            ), "Unattested product dialog blocks the offline fixture"
            button = next(node for node in walk(dialogs[-1])
                          if isinstance(node, Gtk.Button)
                          and str(node.get_label()).replace("_", "") == "Fermer")
            report["pointer_actions"].append(device[0].click_widget(button))
            report["offline_fixture_notifications_acknowledged"] = report.get("offline_fixture_notifications_acknowledged", 0) + 1
            later(150, begin_sync_progress_checks)
            return
        sync_progress_check["original"] = w.service.opening_sync
        start_sync_progress_mode(False)

    def cleanup_sync_coordination():
        if not coordination.get("overrides"):
            return
        w = window[0]
        for owner, name, existed, original in reversed(coordination["overrides"]):
            if existed:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
        coordination["overrides"] = []
        w.service.identities[:] = coordination["identities"]
        if not getattr(w, "_closed", False):
            w._populate_accounts()

    def begin_sync_coordination(task_id, completed):
        """#981: real GTK/TaskRunner, synthetic producer, real local reads."""
        w = window[0]
        coordination.update(identities=list(w.service.identities), overrides=[],
                            reads=[], renders=[], source_calls=[], measurements=[], errors=[])
        def override(owner, name, value):
            coordination["overrides"].append((owner, name, name in vars(owner), vars(owner).get(name)))
            setattr(owner, name, value)
        original_read = w.service.library_entries
        original_render = w._render_library
        def measured_read(**kwargs):
            start = monotonic()
            try:
                assert kwargs.get("scan") is False
                return original_read(**kwargs)
            except BaseException as exc:
                coordination["errors"].append((type(exc).__name__, kwargs.get("scan")))
                raise
            finally:
                coordination["reads"].append((kwargs.get("identity_id"), (monotonic()-start)*1000))
        def measured_render(*args, **kwargs):
            start = monotonic()
            identity = w.service.current_identity.identity_id
            try:
                return original_render(*args, **kwargs)
            finally:
                coordination["renders"].append((identity, (monotonic()-start)*1000))
        override(w.service, "library_entries", measured_read)
        override(w, "_render_library", measured_render)
        # Only the provider refresh is synthetic in this no-plugins fixture;
        # local SQLite reads, identity transitions and GTK rendering stay real.
        def offline_loans(*, force=False, identity_id=None):
            assert w.service.identity_by_id(identity_id) is not None
            return []
        override(w.service, "refresh_identity_loans", offline_loans)
        # Distinct source missions must not reuse the automatic global task.
        for source in ("paris-one", "paris-two"):
            w.runner.submit("Source Paris fictive", lambda sid=source: coordination["source_calls"].append(sid),
                            kind="source-sync", origin="user")
        coordination["task_id"] = task_id
        coordination["completed"] = completed
        coordination["deadline"] = monotonic() + 7
        def sources_ready():
            if (len(coordination["source_calls"]) != 2
                    or any(kind != "opening-sync" for _p, _t, kind in w._active_tasks.values())):
                assert monotonic() < coordination["deadline"]
                later(60, sources_ready)
                return
            select_coordination_identity(1)
        sources_ready()

    def select_coordination_identity(index):
        w = window[0]
        assert w.identity_dropdown.get_sensitive() and w.identity_dropdown.get_mapped()
        capture("10b-before-profile-selection-" + str(index + 1))
        report["pointer_actions"].append(device[0].click_widget(w.identity_dropdown))
        def select():
            capture("10c-profile-selection-popup-" + str(index + 1))
            for key in ["Home" if index == 0 else "End", "Return"]:
                device[0].key(key, True); device[0].key(key, False)
            later(80, lambda: coordination_identity_ready(index))
        later(220, select)

    def coordination_identity_ready(index):
        w = window[0]
        identity = ("camille", "alex", "p0-zoe")[index]
        if (w.service.current_identity.identity_id != identity
                or any(kind != "opening-sync" for _p, _t, kind in w._active_tasks.values())
                or w._library_refresh_pending
                or w._library_loaded_generation != w._identity_context_generation):
            assert monotonic() < coordination["deadline"], (
                "#981 identity/load timeout", index, w.identity_dropdown.get_selected(),
                w.service.current_identity.identity_id,
                [kind for _p, _t, kind in w._active_tasks.values()], w._library_refresh_pending,
                coordination["errors"],
            )
            later(60, lambda: coordination_identity_ready(index))
            return
        assert coordination["task_id"] in w._active_tasks, "#981 opening-sync worker disappeared during profile change"
        assert coordination["opening_calls"] == 1, ("#981 opening-sync coalescing", coordination["opening_calls"])
        assert sorted(coordination["source_calls"]) == ["paris-one", "paris-two"], ("#981 source-sync isolation", len(coordination["source_calls"]))
        assert w._library_loaded_generation == w._identity_context_generation, ("#981 loaded generation differs from active identity", w._library_loaded_generation, w._identity_context_generation)
        assert sum(owner == identity for owner, _ms in coordination["reads"]) == 1, ("#981 unexpected library read count during profile switch", index, sum(owner == identity for owner, _ms in coordination["reads"]))
        before_reads = len(coordination["reads"])
        before_render = len(coordination["renders"])
        controls = w.library_controls.state()
        for _ in range(3):
            w.navigate("search"); w.navigate("library")
        w._restore_navigation_state(w._navigation_state("library"))
        assert len(coordination["reads"]) == before_reads, "#981 cached navigation reloaded library"
        assert len(coordination["renders"]) - before_render == 4, "#981 duplicate route/history render"
        assert w.library_controls.state() == controls
        assert w.service.current_identity.identity_id == identity
        report["checks"]["P0_981_cached_navigation_single_render_" + str(index)] = True
        # An explicit refresh remains an independent operation on this profile.
        coordination["manual_read_start"] = before_reads
        w.refresh_library(False, Gtk.Button())
        later(60, lambda: coordination_manual_done(index))

    def coordination_manual_done(index):
        w = window[0]
        if w._library_refresh_pending or w.view_stack.get_transition_running():
            assert monotonic() < coordination["deadline"], "#981 manual refresh timeout"
            later(60, lambda: coordination_manual_done(index))
            return
        assert len(coordination["reads"]) == coordination["manual_read_start"] + 1, ("#981 manual refresh read count", index, len(coordination["reads"]) - coordination["manual_read_start"])
        assert coordination["task_id"] in w._active_tasks, "#981 opening-sync worker disappeared during manual refresh"
        identity = w.service.current_identity.identity_id
        coordination["measurements"].append({
            "profile": index + 1,
            "reads_ms": [round(ms, 3) for owner, ms in coordination["reads"] if owner == identity],
            "renders_ms": [round(ms, 3) for owner, ms in coordination["renders"] if owner == identity],
            "cached_route_visits": 3, "history_restores": 1, "explicit_refreshes": 1,
        })
        capture("10a-sync-navigation-profile-" + str(index + 1))
        if index in (1, 2):
            if index == 1:
                from cifereca.identities import Identity
                w.service.identities.append(Identity("p0-zoe", "Zoé fictive", directory_name="p0-zoe"))
                w._populate_accounts()
            select_coordination_identity(2 if index == 1 else 0)
            return
        report["sync_coordination"] = {
            "automatic_requests": 5, "automatic_executions": coordination["opening_calls"],
            "distinct_source_executions": len(coordination["source_calls"]),
            "profiles": coordination["measurements"],
            "limits": "Real GTK/local SQLite and rendering; synthetic producer and Paris tasks. No live provider latency, acquisitions or absolute timing gate.",
        }
        report["checks"]["P0_981_three_profiles_during_one_coalesced_opening_and_two_source_tasks"] = True
        completed = coordination["completed"]
        cleanup_sync_coordination()
        completed()

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
                if not deep:
                    coordination["opening_calls"] = coordination.get("opening_calls", 0) + 1
                sync_progress_check["callback"] = progress
                progress({"event": "identity_begin", "label": "Camille"})
                assert gate.wait(8), "P0: sync progress fixture was not released"
                return {"identities": []}

            w.service.opening_sync = offline_sync
            w.run_opening_sync(deep=deep)
            if not deep:
                for _ in range(4):
                    w.run_opening_sync()
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
                    def complete():
                        sync_progress_check["gate"].set()
                        later(60, lambda: await_completion(task_id))
                    begin_sync_coordination(task_id, complete)
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
        gtk_phase_2027("done")
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsNavigationUXE2E")

        def do_activate(self):
            try:
                gtk_phase_2027("window_start")
                w = MainWindow(self, core.config, core.library, core.diagnostics)
                window[0] = w
                # Observe provenance before TaskRunner's intentional redaction.
                # Only the exact missing-local-provider fixture can be tagged;
                # an unrelated generic error remains a blocking product dialog.
                original_present_error = w.runner.present_error
                w.runner.present_error = lambda error: present_offline_fixture_error(
                    original_present_error, error,
                    dialogs=lambda: [node for node in Gtk.Window.list_toplevels()
                                     if isinstance(node, Adw.MessageDialog)],
                )
                w.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
                w.plugin_manager = IsolatedContributions(page_plugin)
                w._rebuild_plugin_pages()
                w.present()
                gtk_phase_2027("window_shown")
                device[0] = X11Input()
                later(500, home_summary_acceptance if args.window_baseline else initial)
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
    # The full baseline additionally runs 563-row render comparisons, sync
    # consent, Settings and three identity changes with their own deadlines.
    # Its old shared 50s watchdog expired during active profile selection
    # (#2203), after all 760px assertions passed. Keep a finite suite budget;
    # individual operation/geometry assertions retain their original bounds.
    watchdog_seconds = 75 if args.window_baseline else 50
    GLib.timeout_add_seconds(
        watchdog_seconds,
        lambda: (fail(f"Timeout Diagnostics navigation {watchdog_seconds}s"), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="GTK fermé sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
