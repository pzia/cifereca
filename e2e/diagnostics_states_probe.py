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
from threading import Event, Thread
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/plugins/diagnostics-page"))
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk
from PIL import ImageGrab

from diagnostics_page_probe import reveal_diagnostics_section, Broker, Context, Host, Tasks, walk
from cifereca.plugin_api import LLMResult
from x11_input import X11Input


class StateBroker(Broker):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.ai_revision = 1
        self.refresh_gate = None
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
        # Permit a TRUE pre-result UI assertion, not a race with the worker.
        # Only the fictitious AI fixture uses this short-lived worker gate.
        gate = self.refresh_gate if self.state == "ai" else None
        if gate is not None and not gate.wait(6.0):
            raise RuntimeError("fixture-refresh-gate-timeout")
        if self.fail:
            raise RuntimeError("fixture-source-indisponible")
        events = [{
            "event_id": f"event-ux-{self.ai_revision:03d}",
            "timestamp": "2026-10-11T05:20:00+00:00"
                if self.ai_revision == 1 else "2026-10-12T07:20:00+00:00",
            "source": "core_events", "module": "core.sync", "severity": "warning",
            "status": "partial", "message": (
                f"Incident simulé révision {self.ai_revision} sans donnée réelle"
            ),
            "secret": "UX_FICTIF_NON_EXPORTABLE",
        }] if self.state == "ai" else []
        return {
            "events": events, "total": len(events), "offset": 0, "next_offset": None,
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


class FictionalLLM:
    """Deterministic opt-in UX fixture: no HTTP client, no network calls."""

    def __init__(self):
        self.calls = 0
        self.requests = []

    def available(self):
        return True

    def destination(self):
        return {
            "available": True, "remote": True, "host": "model.example",
            "model": "ux-fictif", "provider": "fixture-ux",
        }

    def generate(self, request, *, control=None):
        self.calls += 1
        self.requests.append(request)
        # The fake model cites the event actually present in the approved
        # prompt; it must not hallucinate an obsolete fixture ID itself.
        ref = "event-ux-002" if "event-ux-002" in request.prompt else "event-ux-001"
        return LLMResult(
            True, text=f"Hypothèse fictive : rapprocher {ref} avant conclusion.",
            provider="fixture-ux", model="ux-fictif", generated=True, remote=True,
        )


class FictionalLLMContext(Context):
    def __init__(self, broker, tasks, llm):
        super().__init__(broker, tasks)
        self.llm = llm

    def service(self, name):
        return self.llm if name == "llm" else None


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
    ai_fixture = [None]
    ai_refresh_queries_before = [None]
    ai_journals_queries_before = [None]
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
                code = getattr(fn, "__code__", None)
                if code is not None and code.co_filename == __file__:
                    report["checkpoint_line"] = code.co_firstlineno
                save()
                fn()
            except Exception as exc:
                frames = []
                trace = exc.__traceback__
                while trace is not None:
                    if trace.tb_frame.f_code.co_filename == __file__:
                        frames.append(trace.tb_lineno)
                    trace = trace.tb_next
                report["failure_line"] = frames[-1] if frames else 0
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, cb)

    def until(predicate, callback, *, seconds=8):
        # Only a source-verified call line can be published by the CI exporter.
        from inspect import currentframe
        frame = currentframe()
        try:
            caller = frame.f_back if frame is not None else None
            if caller is not None and caller.f_code.co_filename == __file__:
                report["wait_line"] = caller.f_lineno
        finally:
            del frame
        save()
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
        return reveal_diagnostics_section(buttons[0])

    def switch(state):
        if plugin[0] is not None:
            window[0].set_child(None)
            plugin[0].deactivate()
        broker[0] = StateBroker(state)
        tasks = StateTasks()
        host[0] = Host()
        ai_fixture[0] = FictionalLLM() if state == "ai" else None
        context = (
            FictionalLLMContext(broker[0], tasks, ai_fixture[0])
            if state == "ai" else Context(broker[0], tasks)
        )
        plugin[0] = module.DiagnosticsPagePlugin().activate(context)
        root = plugin[0]._build(host[0])
        window[0].set_child(root)
        # This probe directly drives the main Diagnostics scroll adjustment.
        # The outer Gtk.ScrolledWindow is now wrapped by the P0 wheel router.
        scrollers = [w for w in walk(root) if isinstance(w, Gtk.ScrolledWindow)]
        assert scrollers, "Page scroller absent"
        page[0] = scrollers[0]

    def d01():
        switch("empty")
        until(lambda: "Canaux de diagnostic : états indisponibles" in " ".join(labels())
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
        assert not has("Canaux de diagnostic indisponibles — aucune donnée exploitable")
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
        assert has("Aucun événement publié dans l’extrait consultable")
        assert has("24 heures") and has("sources indisponibles ne sont pas évaluées")
        assert has("journaux tronqués peuvent masquer des événements")
        assert not has("Pas d’erreur observée")
        assert not has("Aucune anomalie rapportée")
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
        # D14/#1009: exercise the complete preview -> invalidated preview ->
        # explicit consent journey with a NO-NETWORK fictional LLM. No implicit
        # model call may occur when the page is created.
        switch("ai")
        until(lambda: len(broker[0].event_queries) == 1
              and has("1 événements affichés"), d14_preview_entry, seconds=6)

    def ai_button(label):
        found = [w for w in walk(page[0])
                 if isinstance(w, Gtk.Button) and w.get_label() == label]
        assert len(found) == 1, ("D14 bouton absent", label, len(found))
        return reveal_diagnostics_section(found[0])

    def ai_status():
        return " ".join(labels())

    def click_ai(button, callback):
        # A mapped GTK control may be outside the viewport; scroll the *real*
        # Diagnostics page until its bounds are onscreen before physical XTEST.
        outer = page[0]
        ok, bounds = button.compute_bounds(outer.get_child())
        assert ok and bounds.get_width() > 0, "D14: contrôle sans allocation"
        adjustment = outer.get_vadjustment()
        target = max(0.0, min(
            float(bounds.get_y()) - 110,
            max(0.0, adjustment.get_upper() - adjustment.get_page_size()),
        ))
        adjustment.set_value(target)
        later(lambda: (device[0].click_widget(button), callback()), 240)

    def d14_preview_entry():
        report["stage"] = "D14_no_implicit_ai"
        assert ai_fixture[0] is not None and ai_fixture[0].calls == 0
        assert broker[0].checks == 0
        assert plugin[0].llm is ai_fixture[0]
        assert not ai_button("Autoriser et analyser").get_sensitive()
        assert has("IA non configurée ou non consultée")
        report["checks"]["D14_no_implicit_ai_or_active_check"] = True
        capture("08-d14-before-preview-no-llm-call")
        click_ai(ai_button("Prévisualiser pour l’IA"), d14_wait_preview)

    def d14_wait_preview():
        until(lambda: ai_button("Autoriser et analyser").get_sensitive()
              and has("DISTANT — aucun envoi avant autorisation"),
              d14_preview_ready, seconds=7)

    def d14_preview_ready():
        assert ai_fixture[0].calls == 0
        assert "modèle ux-fictif" in ai_status()
        assert "Le modèle produit des hypothèses, jamais une preuve" in ai_status()
        assert "rapport support : non" in ai_status()
        report["checks"]["D14_remote_destination_disclosed_before_consent"] = True
        assert any(
            "event-ux-001" in view.get_buffer().get_text(
                view.get_buffer().get_start_iter(),
                view.get_buffer().get_end_iter(), False,
            ) for view in walk(page[0]) if isinstance(view, Gtk.TextView)
        ), "D14: l'aperçu initial n'inclut pas l'événement fictif 001"
        capture("09-d14-remote-preview-not-sent")
        # Freshness regression: refresh MUST revoke the previously approved
        # preview if the underlying public broker feed has since changed.
        # Reproduction uses a *real GTK user keystroke*; no real LLM call.
        ai_refresh_queries_before[0] = len(broker[0].event_queries)
        broker[0].ai_revision = 2
        broker[0].refresh_gate = Event()
        refresh = button_tooltip("Actualiser les diagnostics")
        assert refresh.grab_focus() is not False
        later(lambda: d14_refresh_physical(refresh), 210)

    def d14_refresh_physical(refresh):
        assert refresh.has_focus(), "D14: l'actualisation ne reçoit pas le focus"
        device[0].key("space", True)
        device[0].key("space", False)
        report["checks"]["D14_explicit_refresh_keyboard_xtest"] = True
        # XTEST enqueues a key event; wait until GTK dispatches the click
        # and the fake broker enters its BLOCKED worker before asserting.
        until(
            lambda: len(broker[0].event_queries) > ai_refresh_queries_before[0],
            d14_full_refresh_in_flight, seconds=5,
        )

    def d14_full_refresh_in_flight():
        assert broker[0].refresh_gate is not None
        assert not broker[0].refresh_gate.is_set()
        # P0 #1255: fail closed after GTK click, BEFORE the broker replies.
        assert not ai_button("Autoriser et analyser").get_sensitive(), (
            "D14: actualiser Diagnostics laisse le consentement IA actif"
        )
        assert has("Aperçu à actualiser avant toute nouvelle analyse")
        assert not any(
            "event-ux-001" in view.get_buffer().get_text(
                view.get_buffer().get_start_iter(), view.get_buffer().get_end_iter(), False,
            )
            for view in walk(page[0]) if isinstance(view, Gtk.TextView)
        ), "D14: l'ancien prompt IA reste visible après actualisation"
        assert ai_fixture[0].calls == 0
        report["checks"]["D14_full_refresh_revokes_consent_before_broker_result"] = True
        broker[0].refresh_gate.set()  # release simulated slow broker read
        broker[0].refresh_gate = None
        until(
            lambda: len(broker[0].event_queries) > ai_refresh_queries_before[0]
            and not has("Actualisation en cours")
            and d14_updated_event_visible(),
            d14_after_refresh, seconds=7,
        )

    def d14_updated_event_visible():
        # Journal groups are collapsed by default (warning severity), so the
        # new event's provenance cannot be inspected until its *real GTK*
        # expander has been opened. Do not infer revision 002 from counts.
        groups = [
            widget for widget in walk(page[0])
            if isinstance(widget, Gtk.Expander)
            and (widget.get_label() or "").startswith("core.sync · ")
        ]
        if not groups:
            return False
        for group in groups:
            if not group.get_expanded():
                group.set_expanded(True)
        return any("event-ux-002" in label for label in labels())

    def d14_after_refresh():
        # The journal now displays revision 2. Inspect the *unchanged*
        # preview and the consent button separately. This audit may identify
        # a product FAIL without turning an exploratory probe into a
        # fictitious product PASS.
        assert ai_fixture[0].calls == 0
        assert broker[0].checks == 0
        old_preview = any(
            "event-ux-001" in view.get_buffer().get_text(
                view.get_buffer().get_start_iter(),
                view.get_buffer().get_end_iter(), False,
            ) for view in walk(page[0]) if isinstance(view, Gtk.TextView)
        )
        new_journal = any("event-ux-002" in label for label in labels())
        still_sendable = ai_button("Autoriser et analyser").get_sensitive()
        report["audits"]["D14_new_broker_event_visible_after_explicit_refresh"] = new_journal
        report["audits"]["D14_old_ai_preview_still_displayed"] = old_preview
        report["audits"]["D14_stale_consent_button_still_sensitive"] = still_sendable
        report["audits"]["D14_refresh_revokes_outdated_ai_approval"] = not still_sendable
        report["audits"]["D14_llm_calls_before_any_consent"] = ai_fixture[0].calls
        report["checks"]["D14_refresh_changes_public_feed_without_ai_call"] = (
            new_journal and ai_fixture[0].calls == 0
        )
        # Since #1255 is fixed and merged, these are now acceptance
        # assertions, NOT an observational PASS that can hide a product FAIL.
        assert new_journal and not old_preview and not still_sendable, (
            "D14 #1255: ancien aperçu/consentement survivant au refresh",
            new_journal, old_preview, still_sendable,
        )
        report["checks"]["D14_full_refresh_revokes_stale_ai_approval"] = True
        # Two *distinct rendered* screenshots: journal revision 2, then
        # the revoked preview and disabled approval, after settling GTK.
        later(d14_capture_new_revision, 250)

    def d14_capture_new_revision():
        assert d14_updated_event_visible()
        assert ai_fixture[0].calls == 0
        capture("09a-d14-current-journal-revision-002")
        send = ai_button("Autoriser et analyser")
        content = page[0].get_child()
        ok, bounds = send.compute_bounds(content)
        assert ok and bounds.get_width() > 0 and bounds.get_height() > 0
        adjustment = page[0].get_vadjustment()
        adjustment.set_value(max(0.0, min(
            float(bounds.get_y()) - 150,
            max(0.0, adjustment.get_upper() - adjustment.get_page_size()),
        )))
        later(d14_capture_stale_approval, 300)

    def d14_capture_stale_approval():
        assert ai_fixture[0].calls == 0
        assert ai_button("Autoriser et analyser").get_sensitive() == (
            report["audits"]["D14_stale_consent_button_still_sensitive"]
        )
        assert not ai_button("Autoriser et analyser").get_sensitive()
        assert has("Aperçu à actualiser avant toute nouvelle analyse")
        capture("09b-d14-consent-revoked-after-full-refresh")
        # Recreate a genuinely sendable preview on revision 002, then prove
        # the OTHER user-facing refresh entry point revokes it too.
        prep = ai_button("Prévisualiser pour l’IA")
        assert prep.grab_focus() is not False
        later(lambda: d14_repreview_for_journals(prep), 200)

    def d14_repreview_for_journals(prep):
        assert prep.has_focus()
        device[0].key("space", True)
        device[0].key("space", False)
        until(
            lambda: ai_button("Autoriser et analyser").get_sensitive()
            and "event-ux-002" in ai_prompt_text(),
            d14_journals_entry, seconds=7,
        )

    def ai_prompt_text():
        # Use the text shown in the REAL GTK preview expander, not broker
        # state or a model callback, to attest the approved revision.
        panes = [
            exp for exp in walk(page[0]) if isinstance(exp, Gtk.Expander)
            and exp.get_label() == "Données expurgées proposées à l’IA"
        ]
        assert len(panes) == 1
        views = [view for view in walk(panes[0]) if isinstance(view, Gtk.TextView)]
        assert len(views) == 1
        buffer = views[0].get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def d14_journals_entry():
        assert ai_fixture[0].calls == 0
        assert "event-ux-001" not in ai_prompt_text()
        ai_journals_queries_before[0] = len(broker[0].event_queries)
        broker[0].refresh_gate = Event()
        # Reading the IA button changes tabs via reveal_diagnostics_section.
        # Verify consent BEFORE focusing the Journaux refresh target, never
        # between the physical focus check and the XTEST Space key.
        assert ai_button("Autoriser et analyser").get_sensitive()
        refresh = button_tooltip("Actualiser les journaux sans synchronisation")
        assert refresh.grab_focus() is not False
        later(lambda: d14_journals_refresh_xtest(refresh), 210)

    def d14_journals_refresh_xtest(refresh):
        assert refresh.has_focus(), "D14: bouton Journaux sans focus"
        device[0].key("space", True)
        device[0].key("space", False)
        until(
            lambda: len(broker[0].event_queries) > ai_journals_queries_before[0],
            d14_journals_refresh_in_flight, seconds=5,
        )

    def d14_journals_refresh_in_flight():
        assert broker[0].refresh_gate is not None
        assert not broker[0].refresh_gate.is_set()
        # Revocation must precede the simulated blocked broker reply.
        assert not ai_button("Autoriser et analyser").get_sensitive(), (
            "D14: rafraîchir les journaux laisse le consentement IA actif"
        )
        assert ai_prompt_text() == "", "D14: ancien prompt visible après refresh journaux"
        assert has("Aperçu à actualiser avant toute nouvelle analyse")
        assert ai_fixture[0].calls == 0
        report["checks"]["D14_journals_refresh_revokes_consent_before_broker_result"] = True
        broker[0].refresh_gate.set()
        broker[0].refresh_gate = None
        until(
            lambda: len(broker[0].event_queries) > ai_journals_queries_before[0]
            and not has("Actualisation en cours") and d14_updated_event_visible(),
            d14_journals_after_refresh, seconds=7,
        )

    def d14_journals_after_refresh():
        assert not ai_button("Autoriser et analyser").get_sensitive()
        assert ai_prompt_text() == ""
        assert ai_fixture[0].calls == 0
        assert broker[0].checks == 0
        report["checks"]["D14_journals_refresh_stays_revoked_after_broker_result"] = True
        later(d14_capture_journals_revocation, 230)

    def d14_capture_journals_revocation():
        send = ai_button("Autoriser et analyser")
        ok, bounds = send.compute_bounds(page[0].get_child())
        assert ok
        adjustment = page[0].get_vadjustment()
        adjustment.set_value(max(0.0, min(
            float(bounds.get_y()) - 150,
            max(0.0, adjustment.get_upper() - adjustment.get_page_size()),
        )))
        later(d14_journals_repreview_to_scope, 250)

    def d14_journals_repreview_to_scope():
        assert not ai_button("Autoriser et analyser").get_sensitive()
        capture("09c-d14-consent-revoked-after-journals-refresh")
        # Recreate a valid consent opportunity before the EXISTING
        # scope-change invalidation test. Keep each mechanism independent.
        prep = ai_button("Prévisualiser pour l’IA")
        assert prep.grab_focus() is not False
        later(lambda: d14_repreview_for_scope(prep), 190)

    def d14_repreview_for_scope(prep):
        assert prep.has_focus()
        device[0].key("space", True)
        device[0].key("space", False)
        until(
            lambda: ai_button("Autoriser et analyser").get_sensitive()
            and "event-ux-002" in ai_prompt_text(),
            d14_scope_change, seconds=7,
        )

    def d14_scope_change():
        assert ai_fixture[0].calls == 0
        selected = [w for w in walk(page[0])
                    if isinstance(w, Gtk.CheckButton)
                    and w.get_label() == "Inclure les diagnostics structurés Core (lecture seule)"]
        assert len(selected) == 1
        selected[0].set_active(True)
        later(d14_invalidated, 130)

    def d14_invalidated():
        assert not ai_button("Autoriser et analyser").get_sensitive()
        assert has("Aperçu à actualiser avant toute nouvelle analyse")
        assert ai_fixture[0].calls == 0
        report["checks"]["D14_changed_scope_invalidates_consent"] = True
        capture("10-d14-preview-invalidated-by-scope")
        # A changed preview collapses its content and shifts scroll geometry.
        # Keyboard traversal is the stable UX route for this SECOND preview;
        # the initial preview and final approval still use physical XTEST mouse.
        prep = ai_button("Prévisualiser pour l’IA")
        assert prep.grab_focus() is not False
        later(lambda: d14_keyboard_repreview(prep), 200)

    def d14_keyboard_repreview(prep):
        assert prep.has_focus(), "D14: le bouton Aperçu ne reçoit plus le focus"
        device[0].key("space", True)
        device[0].key("space", False)
        report["checks"]["D14_repreview_keyboard_space_xtest"] = True
        d14_wait_repreview()

    def d14_wait_repreview():
        until(lambda: ai_button("Autoriser et analyser").get_sensitive()
              and has("diagnostics structurés : oui"),
              d14_ready_to_consent, seconds=7)

    def d14_ready_to_consent():
        assert ai_fixture[0].calls == 0
        assert broker[0].checks == 0
        report["checks"]["D14_repreview_still_no_llm_call"] = True
        capture("11-d14-explicit-approval-available")
        # The expanded preview can steal scroll space while the pointer moves.
        # A focused button + physical XTEST Space is an explicit real-user
        # confirmation without relying on a stale click coordinate.
        approve = ai_button("Autoriser et analyser")
        assert approve.grab_focus() is not False
        later(lambda: d14_keyboard_approve(approve), 220)

    def d14_keyboard_approve(approve):
        assert approve.has_focus(), "D14: le consentement IA n'accepte pas le focus"
        assert ai_fixture[0].calls == 0
        device[0].key("space", True)
        device[0].key("space", False)
        report["checks"]["D14_approval_keyboard_space_xtest"] = True
        d14_wait_generated()

    def d14_wait_generated():
        until(lambda: ai_fixture[0].calls == 1
              and has("Analyse IA générée ; vérifier toutes les références avant décision."),
              d14_after_consent, seconds=8)

    def d14_after_consent():
        assert ai_fixture[0].calls == 1, "D14: plus d'un appel fictif au LLM"
        assert broker[0].checks == 0, "D14: vérification active implicite"
        assert not ai_button("Autoriser et analyser").get_sensitive()
        assert ai_button("Copier JSON IA").get_sensitive()
        assert len(ai_fixture[0].requests) == 1
        prompt = ai_fixture[0].requests[0].prompt
        assert "event-ux-002" in prompt
        assert "event-ux-001" not in prompt
        assert "UX_FICTIF_NON_EXPORTABLE" not in prompt
        views = [w for w in walk(page[0]) if isinstance(w, Gtk.TextView)]
        texts = [v.get_buffer().get_text(
            v.get_buffer().get_start_iter(), v.get_buffer().get_end_iter(), False,
        ) for v in views]
        assert any("hypothèses NON vérifiées" in value
                   and "event-ux-002" in value for value in texts)
        report["checks"]["D14_exactly_one_explicit_ai_call"] = True
        report["checks"]["D14_inference_not_confused_with_core_proof"] = True
        report["checks"]["D14_fictional_secret_not_sent_to_llm"] = True
        report["audits"]["D14_real_remote_llm_or_temporal_freshness_tested"] = False
        capture("12-d14-generated-inference-disclaimer")
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
