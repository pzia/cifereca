#!/usr/bin/env python3
"""UX #1011 D01-D04: isolated REAL GTK Diagnostics with deterministic brokers.

Tests real states and a slow active check; observes but does not fake product
capabilities. The full MainWindow navigation remains a separate scenario.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/plugins/diagnostics-page"))
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk
from PIL import ImageGrab

from diagnostics_page_probe import Broker, Context, Host, Tasks, walk
from x11_input import X11Input


class StateBroker(Broker):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.fail = state == "unavailable"

    def system_status(self):
        if self.fail:
            raise RuntimeError("fixture-broker-indisponible")
        if self.state == "empty":
            return {}
        return {"nombre_anomalies": 0, "releve": "période de 24 heures"}

    def inventory(self):
        return ()

    def snapshot(self):
        return ()

    def export_support_text(self):
        return "Rapport support local expurgé — aucune collecte réseau"

    def events(self, **query):
        self.event_queries.append(dict(query))
        if self.fail:
            raise RuntimeError("fixture-source-indisponible")
        return {
            "events": [], "total": 0, "offset": 0, "next_offset": None,
            "sources": [] if self.state == "empty" else [
                {"source": "core_events", "status": "available"},
                *([{"source": "app_log", "status": "unavailable"}]
                  if self.state == "partial" else []),
            ],
        }

    def active_checks(self):
        self.checks += 1
        sleep(1.15)  # Only in the daemon worker, NEVER the GTK main thread.
        return [{"status": "ok", "message": "Contrôle local fictif terminé"}]


class SlowHandle:
    def __init__(self, fn):
        self.future = Future()
        self.control = self
        self._cancelled = False

        def worker():
            try:
                value = fn(None)
                if not self.future.done():
                    self.future.set_result(value)
            except Exception as exc:
                if not self.future.done():
                    self.future.set_exception(exc)

        Thread(target=worker, name="ux-diag-slow-fixture", daemon=True).start()

    def add_done_callback(self, callback):
        self.future.add_done_callback(lambda _f: callback(self))

    def result(self):
        return self.future.result()

    def done(self):
        return self.future.done()

    def cancelled(self):
        return self._cancelled

    def cancel(self):
        self._cancelled = True


class StateTasks(Tasks):
    def submit(self, fn, *, label, kind):
        if label in {"Consultation des journaux", "Vérifications diagnostics"}:
            # Reproduce real TaskRunner completion semantics: failures in
            # broker.events() are delivered asynchronously via result().
            # Calling fn inline would instead exercise the submission-error
            # path, which has a different (also valid) UI message.
            self.calls.append((label, kind))
            return SlowHandle(fn)
        return super().submit(fn, label=label, kind=kind)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(1366,))
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK/XTEST/Xvfb obligatoire")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    module = importlib.import_module("cifereca_diagnostics_page")
    report = {
        "scenario": "diagnostics-states-1366",
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "status": "RUNNING", "screenshots": [], "checks": {}, "audits": {},
        "scope": "vrai widget Diagnostics GTK4, services simulés, aucun réseau",
    }
    window = [None]
    plugin = [None]
    broker = [None]
    page = [None]
    host = [None]
    device = [None]
    checks_started_at = [None]
    rc = [1]
    tick = [0]

    def save():
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def capture(name):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert image.size == (1366, 900), image.size
        assert image.getbbox() is not None
        file = out / (name + ".png")
        image.save(file)
        shutil.copyfile(file, out / "screen.png")
        report["screenshots"].append({
            "name": name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()
        })

    def fail(reason):
        if report["status"] != "RUNNING":
            return
        report.update(status="FAIL", error=str(reason))
        try:
            capture("failure")
        except Exception:
            pass
        save()
        app.quit()

    def later(fn, ms=120):
        def cb():
            try:
                fn()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, cb)

    def until(predicate, callback, *, seconds=8):
        deadline = monotonic() + seconds

        def poll():
            if predicate():
                callback()
            elif monotonic() >= deadline:
                raise AssertionError("Attente GTK expirée")
            else:
                later(poll, 90)
        later(poll, 120)

    def labels():
        return [w.get_text() or "" for w in walk(page[0])
                if isinstance(w, Gtk.Label)]

    def has(text):
        return any(text in label for label in labels())

    def button_tooltip(value):
        buttons = [w for w in walk(page[0])
                   if isinstance(w, Gtk.Button) and w.get_tooltip_text() == value]
        assert len(buttons) == 1, (value, len(buttons))
        return buttons[0]

    def switch(state):
        if plugin[0] is not None:
            window[0].set_child(None)
            plugin[0].deactivate()
        broker[0] = StateBroker(state)
        tasks = StateTasks()
        host[0] = Host()
        plugin[0] = module.DiagnosticsPagePlugin().activate(Context(broker[0], tasks))
        root = plugin[0]._build(host[0])
        window[0].set_child(root)
        # This probe directly drives the main Diagnostics scroll adjustment.
        # The outer Gtk.ScrolledWindow is now wrapped by the P0 wheel router.
        scrollers = [w for w in walk(root) if isinstance(w, Gtk.ScrolledWindow)]
        assert scrollers, "Page scroller absent"
        page[0] = scrollers[0]

    def d01():
        switch("empty")
        until(lambda: "Sources non alimentées" in " ".join(labels())
              and len(broker[0].event_queries) == 1, verify_d01)

    def verify_d01():
        assert has("Aucun événement")
        assert not has("Pas d'erreur observée")  # Empty source != healthy.
        assert broker[0].checks == 0
        report["checks"]["D01_no_sources_not_healthy"] = True
        capture("00-sources-non-alimentees")
        switch("available")
        until(lambda: has("0 événements affichés") and
              len(broker[0].event_queries) == 1, verify_d02)

    def verify_d02():
        assert has("Alimentée")
        assert not has("Sources non alimentées — aucune donnée disponible")
        assert broker[0].checks == 0
        # D02 requires an EXPLICIT zero-anomaly diagnosis with a time window.
        # The generic '0 events' header alone must not be misreported as PASS.
        message = " ".join(labels()).casefold()
        explicit = "pas d'erreur observée" in message or "pas d’erreur observée" in message
        assert explicit and "24 heures" in message
        assert "ne garantit pas" in message
        report["audits"]["D02_explicit_zero_anomaly_with_period"] = explicit
        report["checks"]["D02_zero_events_vs_source_available"] = True
        capture("01-source-alimentee-sans-evenement")
        switch("partial")
        until(lambda: has("Couverture partielle") and
              len(broker[0].event_queries) == 1, verify_d02_partial)

    def verify_d02_partial():
        assert has("Aucune anomalie rapportée dans les sources consultées")
        assert has("24 heures") and has("sources indisponibles ne sont pas évaluées")
        assert not has("Pas d’erreur observée")
        assert broker[0].checks == 0 and plugin[0].llm is None
        report["checks"]["D02_partial_coverage_not_global_health"] = True
        capture("01b-couverture-partielle-sans-evenement")
        switch("unavailable")
        # Immediate fixture raises directly in tasks.submit(): the plugin
        # uses its explicit "Source indisponible : RuntimeError" UI state.
        # Async broker failure instead displays "Données indisponibles".
        until(lambda: (
                has("Source indisponible : RuntimeError")
                or has("Données indisponibles")
              ) and len(broker[0].event_queries) == 1, verify_d03)

    def verify_d03():
        assert page[0].get_mapped(), "Fenêtre GTK indisponible"
        assert any("Diagnostics indisponibles" in toast for toast in host[0].toasts)
        refresh = button_tooltip("Actualiser les diagnostics")
        assert refresh.get_sensitive()
        report["audits"]["D03_visible_error_state"] = (
            "Source indisponible : RuntimeError" if
            has("Source indisponible : RuntimeError") else "Données indisponibles"
        )
        report["checks"]["D03_error_contextualized_and_ui_responsive"] = True
        capture("02-broker-indisponible")
        # A deliberate user retry after the outage, never an automatic call.
        broker[0].fail = False
        refresh.emit("clicked")
        until(lambda: has("0 événements affichés") and
              len(broker[0].event_queries) == 2, verify_recovery)

    def verify_recovery():
        assert broker[0].checks == 0
        report["checks"]["D03_explicit_retry_recovers"] = True
        capture("03-reprise-explicite")
        switch("slow")
        until(lambda: has("0 événements affichés") and
              len(broker[0].event_queries) == 1, d04)

    def d04():
        assert broker[0].checks == 0
        # D14: the three zones remain separate in the real GTK widget.
        for section in (
            "Rapport support (copie expurgée)",
            "Vérification active",
            "Analyse IA (facultative)",
        ):
            assert has(section), ("Section D14 manquante", section)
        assert has("IA non configurée ou non consultée")
        assert not any("Analyse Diagnostics IA explicite" in str(row)
                       for row in host[0].toasts)
        report["checks"]["D14_three_distinct_sections_visible"] = True
        send_buttons = [
            w for w in walk(page[0])
            if isinstance(w, Gtk.Button) and w.get_label() == "Autoriser et analyser"
        ]
        assert len(send_buttons) == 1 and not send_buttons[0].get_sensitive()
        assert plugin[0].llm is None
        report["checks"]["D14_ai_absent_and_explicit_send_disabled"] = True
        check = button_tooltip("Vérifier les services configurés et runtimes")
        assert check.get_mapped()
        # The diagnostic controls can sit below the outer scroll viewport;
        # mapped != onscreen. Scroll based on real widget bounds before XTEST.
        content = page[0].get_child()
        ok, bounds = check.compute_bounds(content)
        assert ok, "Position GTK de Vérifier introuvable"
        adjustment = page[0].get_vadjustment()
        target = max(0, min(
            float(bounds.get_y()) - 65,
            adjustment.get_upper() - adjustment.get_page_size(),
        ))
        adjustment.set_value(target)
        report["checks"]["D04_check_button_scrolled_into_view"] = True
        later(lambda: click_slow_check(check), 220)

    def click_slow_check(check):
        checks_started_at[0] = monotonic()
        report["ticks_at_click"] = tick[0]
        report["check_click"] = device[0].click_widget(check)
        until(lambda: has("Vérification en cours") and broker[0].checks == 1,
              during_slow_check, seconds=4)

    def during_slow_check():
        assert not button_tooltip("Vérifier les services configurés et runtimes").get_sensitive()
        assert monotonic() - checks_started_at[0] < 1.15, "Le contrôle a bloqué l'UI"
        # Continue real GTK interaction while worker is still busy.
        expanders = [w for w in walk(page[0])
                     if isinstance(w, Gtk.Expander)
                     and w.get_label() == "Ouvrir le rapport support"]
        assert len(expanders) == 1
        expanders[0].set_expanded(True)
        assert expanders[0].get_expanded()
        assert tick[0] > report["ticks_at_click"], "Boucle GTK figée"
        assert has("Vérification en cours")
        assert has("IA non configurée ou non consultée")
        assert not has("Analyse IA en cours")
        # A support report remains available but was not generated by the
        # asynchronous active check and is not an AI inference.
        expander = expanders[0]
        views = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        assert len(views) == 1
        buff = views[0].get_buffer()
        visible = buff.get_text(buff.get_start_iter(), buff.get_end_iter(), False)
        assert "Rapport support local expurgé" in visible
        assert "Contrôle local fictif terminé" not in visible
        assert "hypothèses" not in visible.lower()
        report["checks"]["D14_support_active_and_ai_states_independent_during_check"] = True
        report["checks"]["D04_active_check_does_not_block_GTK"] = True
        report["audits"]["D04_full_mainwindow_navigation"] = False
        capture("04-verification-lente-en-cours")
        until(lambda: has("Contrôle local fictif terminé"),
              d04_complete, seconds=6)

    def d04_complete():
        assert broker[0].checks == 1, "Contrôle lancé plusieurs fois"
        assert button_tooltip("Vérifier les services configurés et runtimes").get_sensitive()
        assert len(broker[0].event_queries) == 1
        report["checks"]["D04_finished_and_button_reenabled"] = True
        assert has("Contrôle local fictif terminé")
        assert not has("Vérification en cours")
        assert has("IA non configurée ou non consultée")
        assert not has("Analyse IA générée")
        assert plugin[0].llm is None
        report["checks"]["D14_active_result_not_confused_with_ai"] = True
        # Distinct visual panels exist, but no evidence of freshness on the
        # support report or a live configured LLM is claimed here.
        report["audits"]["D14_support_report_freshness_explicit"] = False
        report["audits"]["D14_three_states_after_real_broker_coverage"] = False
        capture("05-verification-lente-terminee")
        # D16: resize the *visible GTK window* on the 1366x900 Xvfb
        # display, not the Xvfb screen itself. Do not infer mobile support.
        report["D16_window_before_resize"] = [
            window[0].get_width(), window[0].get_height()
        ]
        window[0].set_default_size(1240, 760)
        until(lambda: window[0].get_width() <= 1260
              and window[0].get_height() <= 790,
              resized_narrow, seconds=7)

    def measure_actions():
        outer = page[0]
        adjustment = outer.get_hadjustment()
        overflow = max(0, adjustment.get_upper() - adjustment.get_page_size())
        controls = [
            button_tooltip("Actualiser les diagnostics"),
            button_tooltip("Vérifier les services configurés et runtimes"),
            button_tooltip("Copier le diagnostic support expurgé"),
        ]
        assert all(button.get_mapped() and button.get_sensitive()
                   for button in controls), "Action Diagnostics inaccessible"
        content = outer.get_child()
        measured = []
        for button in controls:
            ok, bounds = button.compute_bounds(content)
            assert ok, "Action sans allocation GTK"
            assert bounds.get_width() > 0 and bounds.get_height() > 0
            measured.append({
                "tooltip": button.get_tooltip_text(),
                "x": round(bounds.get_x(), 2),
                "width": round(bounds.get_width(), 2),
            })
        return round(overflow, 2), measured

    def resized_narrow():
        assert window[0].get_width() >= 1140
        assert window[0].get_height() >= 650
        assert page[0].get_mapped()
        assert has("Rapport support") and has("Analyse IA")
        overflow, buttons = measure_actions()
        report["D16_narrow_window"] = {
            "size": [window[0].get_width(), window[0].get_height()],
            "horizontal_overflow": overflow,
            "action_geometry": buttons,
        }
        assert overflow < 5, ("Scroll horizontal global inattendu", overflow)
        report["checks"]["D16_1366_display_narrow_no_horizontal_overflow"] = True
        # No service trigger: just focus an existing visible control while
        # keeping the previously completed active-check result intact.
        refresh = button_tooltip("Actualiser les diagnostics")
        assert refresh.grab_focus() is not False
        until(lambda: refresh.has_focus(),
              narrow_actions_focus, seconds=4)

    def narrow_actions_focus():
        assert has("Contrôle local fictif terminé")
        assert has("IA non configurée ou non consultée")
        report["checks"]["D16_narrow_actions_keyboard_reachable"] = True
        capture("06-fenetre-1240-sur-ecran-1366")
        window[0].set_default_size(1300, 790)
        until(lambda: window[0].get_width() >= 1280
              and window[0].get_height() >= 770,
              resized_restored, seconds=7)

    def resized_restored():
        overflow, buttons = measure_actions()
        report["D16_restored_window"] = {
            "size": [window[0].get_width(), window[0].get_height()],
            "horizontal_overflow": overflow,
            "action_geometry": buttons,
        }
        assert overflow < 5, ("Débordement au retour", overflow)
        assert has("Contrôle local fictif terminé")
        assert broker[0].checks == 1
        assert len(broker[0].event_queries) == 1
        assert plugin[0].llm is None
        report["checks"]["D16_restore_without_losing_diagnostics_state"] = True
        capture("07-fenetre-restauree-1300")
        report["unmet_criteria"] = [
            key for key, ok in (
                ("D02: pas d'erreur observée explicite et daté",
                 report["audits"]["D02_explicit_zero_anomaly_with_period"]),
                ("D04: navigation complète MainWindow pendant contrôle",
                 report["audits"]["D04_full_mainwindow_navigation"]),
                ("D14: fraîcheur/provenance du rapport et LLM configuré non éprouvés",
                 report["audits"]["D14_support_report_freshness_explicit"]),
            ) if not ok
        ]
        report["audits"]["D16_full_multi_monitor_coverage"] = False
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    def heartbeat():
        tick[0] += 1
        return report["status"] == "RUNNING"

    class Application(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsStatesE2E")

        def do_activate(self):
            window[0] = Gtk.ApplicationWindow(application=self)
            window[0].set_title("Diagnostics UX — états E2E")
            window[0].set_default_size(1300, 790)
            device[0] = X11Input()
            window[0].present()
            GLib.timeout_add(80, heartbeat)
            later(d01, 140)

        def do_shutdown(self):
            if plugin[0] is not None:
                plugin[0].deactivate()
            if device[0] is not None:
                device[0].close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        40, lambda: (fail("Timeout GTK états 40s"), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="GTK fermé sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
