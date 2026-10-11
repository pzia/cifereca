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
        if label == "Vérifications diagnostics":
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
        page[0] = plugin[0]._build(host[0])
        window[0].set_child(page[0])

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
        report["audits"]["D02_explicit_zero_anomaly_with_period"] = explicit
        report["checks"]["D02_zero_events_vs_source_available"] = True
        capture("01-source-alimentee-sans-evenement")
        switch("unavailable")
        until(lambda: has("Données indisponibles") and
              len(broker[0].event_queries) == 1, verify_d03)

    def verify_d03():
        assert page[0].get_mapped(), "Fenêtre GTK indisponible"
        assert any("Diagnostics indisponibles" in toast for toast in host[0].toasts)
        refresh = button_tooltip("Actualiser les diagnostics")
        assert refresh.get_sensitive()
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
        check = button_tooltip("Vérifier les services configurés et runtimes")
        # Keep the click on a mapped, visible GTK control.
        assert check.get_mapped()
        checks_started_at[0] = monotonic()
        report["ticks_at_click"] = tick[0]
        device[0].click_widget(check)
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
        capture("05-verification-lente-terminee")
        report["unmet_criteria"] = [
            key for key, ok in (
                ("D02: pas d'erreur observée explicite et daté",
                 report["audits"]["D02_explicit_zero_anomaly_with_period"]),
                ("D04: navigation complète MainWindow pendant contrôle",
                 report["audits"]["D04_full_mainwindow_navigation"]),
            ) if not ok
        ]
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
