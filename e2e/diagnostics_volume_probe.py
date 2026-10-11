#!/usr/bin/env python3
"""UX #1011 D06, D08-D12: high-volume diagnostics real GTK with safe fixtures.

A deterministic read-only broker returns at most 50 of 10,000 synthetic events
per request. No private accounts/providers, network, active checks or LLM.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "e2e"),
    str(ROOT / "src/plugins/diagnostics-page"),
    str(ROOT / "environnements/openai-chat-debian"),
]

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk
from PIL import ImageGrab
from diagnostics_page_probe import Broker, Context, Host, Tasks, walk


MODULES = ("core.sync", "paris.lending", "kobo.catalog", "adept.runtime", "plugins.read")
TOTAL = 10000
LONG_PATH = "/home/fixture/secret/" + "very-long-private-path-segment/" * 45


class VolumeBroker(Broker):
    def __init__(self):
        super().__init__()
        self.generated = 0
        self.requests = []

    def system_status(self):
        return {"fixture": "Volume synthétique 10 000 événements, sources locales"}

    def snapshot(self):
        return ()

    def inventory(self):
        return ()

    def export_support_text(self):
        return "RAPPORT SUPPORT EXPURGÉ — volume fictif, aucun secret"

    def _row(self, i):
        mod = MODULES[i % len(MODULES)]
        case = i // len(MODULES)
        stamp = f"2026-10-11T{(i // 60) % 24:02d}:{i % 60:02d}:00+00:00"
        message = f"Événement fictif {i:05d} sans données personnelles"
        level = "info"
        detail = {}
        if mod == "adept.runtime" and case == 0:
            level = "error"
            message = "ADEPT code 10 — traces libgourou absentes ; cause inconnue"
            detail = {"path": LONG_PATH, "reason": "Non établie"}
        if mod == "paris.lending" and case == 0:
            message = "Paris retour demandé et accepté ; disparition du prêt NON confirmée"
            detail = {"request": "accepted", "loan_deleted": False}
        if mod == "paris.lending" and case == 1:
            message = "Prêt partagé : profil-pseudo-A — référence de prêt commune"
            detail = {"identity_id": "profil-pseudo-A", "loan_ref": "pret-fictif-001"}
        if mod == "paris.lending" and case == 2:
            message = "Prêt partagé : profil-pseudo-B — référence de prêt commune"
            detail = {"identity_id": "profil-pseudo-B", "loan_ref": "pret-fictif-001"}
        if mod == "kobo.catalog" and case == 0:
            level = "error"
            message = "Erreur fictive longue : " + "échec temporaire " * 45 + LONG_PATH
            detail = {"path": LONG_PATH, "secret": "SECRET-FICTIF-NE-PAS-AFFICHER"}
        return {
            "event_id": f"ux-evt-{i:05d}", "timestamp": stamp,
            "source": "app_log" if i % 7 == 0 else "core_events",
            "module": mod, "severity": level, "status": "partial" if level == "error" else "ok",
            "message": message, "detail": detail,
        }

    def events(self, **query):
        self.requests.append(dict(query))
        self.event_queries.append(dict(query))
        offset = int(query.get("offset", 0))
        limit = min(int(query.get("limit", 50)), 50)
        source = query.get("source", "all")
        module = str(query.get("module", "")).casefold().strip()
        severity = str(query.get("severity", "")).casefold().strip()
        # Generate only the bounded reply, with a streaming virtual filter.
        matching = 0
        rows = []
        for i in range(TOTAL):
            typ = "app_log" if i % 7 == 0 else "core_events"
            mod = MODULES[i % len(MODULES)]
            lvl = "error" if (mod in ("adept.runtime", "kobo.catalog") and i < 6) else "info"
            if source not in ("all", typ):
                continue
            if module and not mod.startswith(module):
                continue
            if severity and lvl != severity:
                continue
            if offset <= matching < offset + limit:
                rows.append(self._row(i))
                self.generated += 1
            matching += 1
        return {
            "events": rows, "offset": offset, "total": matching,
            "next_offset": offset + len(rows) if offset + len(rows) < matching else None,
            "sources": [
                {"source": "core_events", "status": "available"},
                {"source": "app_log", "status": "partial", "reason": "rotation fictive"},
            ],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", type=int, choices=(1366,), required=True)
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK4 X11/Xvfb requis")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    broker, tasks, host = VolumeBroker(), Tasks(), Host()
    module = importlib.import_module("cifereca_diagnostics_page")
    plugin = module.DiagnosticsPagePlugin().activate(Context(broker, tasks))
    report = {
        "scenario": "diagnostics-volume-1366",
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "status": "RUNNING", "checks": {}, "audits": {}, "screenshots": [],
        "broker_total": TOTAL, "scope": "vrai widget GTK, broker synthétique paginé",
    }
    window, page = [None], [None]
    rc = [1]
    started = monotonic()

    def save():
        report["elapsed_seconds"] = round(monotonic() - started, 3)
        report["event_queries"] = len(broker.event_queries)
        report["generated_event_rows"] = broker.generated
        report["active_checks"] = broker.checks
        (out / "screen.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def screenshot(stage):
        im = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert im.size == (1366, 900)
        assert im.getbbox() is not None
        file = out / (stage + ".png")
        im.save(file)
        shutil.copyfile(file, out / "screen.png")
        report["screenshots"].append({"name": stage, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        report.update(status="FAIL", error=str(exc))
        try:
            screenshot("failure")
        except Exception:
            pass
        save()
        app.quit()

    def later(callback, ms=120):
        def cb():
            try:
                callback()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, cb)

    def until(predicate, then, seconds=8):
        deadline = monotonic() + seconds
        def poll():
            if predicate():
                then()
            elif monotonic() >= deadline:
                raise AssertionError("Timeout attente widget GTK")
            else:
                later(poll, 100)
        later(poll, 130)

    def widgets(cls):
        return [w for w in walk(page[0]) if isinstance(w, cls)]

    def labels():
        return [w.get_text() or "" for w in widgets(Gtk.Label)]

    def seen(fragment):
        return any(fragment in l for l in labels())

    def find_button(name):
        rows = [w for w in widgets(Gtk.Button) if w.get_label() == name]
        assert len(rows) == 1, (name, len(rows))
        return rows[0]

    def module_entry():
        rows = [w for w in widgets(Gtk.Entry)
                if w.get_placeholder_text() == "Préfixe module (Entrée pour appliquer)"]
        assert len(rows) == 1
        return rows[0]

    def query_module(mod, then):
        previous = len(broker.requests)
        entry = module_entry()
        entry.set_text(mod)
        entry.emit("activate")
        until(lambda: len(broker.requests) > previous and
              seen("événements affichés"), then)

    def start():
        assert seen("50 événements affichés")
        assert seen("10000 disponibles")
        assert len(broker.requests) == 1
        assert broker.requests[0]["limit"] == 50
        assert broker.generated == 50
        assert seen("core.sync") and seen("paris.lending")
        assert seen("kobo.catalog") and seen("adept.runtime") and seen("plugins.read")
        assert "Journal partiel" in " ".join(labels())
        assert len(widgets(Gtk.Label)) < 1200, "Rendu de 10000 widgets interdit"
        assert broker.checks == 0
        report["checks"]["D06_10000_events_50_rows_bounded"] = True
        screenshot("00-10000-evenements-premiere-page")
        next_btn = find_button("Suivant")
        assert next_btn.get_sensitive()
        next_btn.emit("clicked")
        until(lambda: len(broker.requests) == 2 and
              seen("page à partir de 51"), next_page)

    def next_page():
        assert broker.requests[-1]["offset"] == 50
        assert broker.generated == 100
        assert len(widgets(Gtk.Label)) < 1200
        assert seen("10000 disponibles")
        report["checks"]["D06_stable_pagination_next_50"] = True
        screenshot("01-page-51-sur-10000")
        query_module("adept", adept_case)

    def adept_case():
        assert broker.requests[-1]["module"] == "adept"
        assert broker.requests[-1]["offset"] == 0, "Filtre module doit remettre à zéro l'offset"
        assert seen("ADEPT code 10")
        assert seen("traces libgourou absentes")
        assert not seen("Adobe refuse") and not seen("cause confirmée")
        report["checks"]["D10_adept_code10_no_invented_adobe_cause"] = True
        report["checks"]["D09_adept_module_filter"] = True
        screenshot("02-adept-code10-absence-preuve")
        query_module("paris", paris_case)

    def paris_case():
        assert broker.requests[-1]["module"] == "paris"
        assert seen("Paris retour demandé et accepté")
        assert seen("disparition du prêt NON confirmée")
        assert not seen("Prêt disparu")
        report["checks"]["D11_paris_return_requested_not_confirmed"] = True
        report["checks"]["D09_paris_module_filter"] = True
        # Grouped rows may remain collapsed: inspect real widget text including labels.
        pseudo_A = seen("profil-pseudo-A")
        pseudo_B = seen("profil-pseudo-B")
        report["audits"]["D12_distinct_pseudonymous_loan_profiles_visible"] = pseudo_A and pseudo_B
        report["audits"]["D12_link_proven_by_broker"] = False
        screenshot("03-paris-retour-profils")
        query_module("kobo", kobo_case)

    def kobo_case():
        assert broker.requests[-1]["module"] == "kobo"
        assert seen("Erreur fictive longue")
        assert len(widgets(Gtk.Label)) < 1200
        visible = " ".join(labels())
        assert LONG_PATH not in visible, "Chemin privé complet visible dans liste"
        report["checks"]["D08_long_path_redacted_list_and_stable_layout"] = True
        report["audits"]["D08_complete_redacted_long_detail_available"] = False
        screenshot("04-kobo-erreur-chemin-long")
        # Filter period is really sent as a bounded UTC 'since' query.
        selectors = [w for w in widgets(Gtk.DropDown) if w.get_tooltip_text() == "Période de la consultation"]
        assert len(selectors) == 1
        before = len(broker.requests)
        selectors[0].set_selected(3)  # 7 jours
        until(lambda: len(broker.requests) > before and seen("événements affichés"), period_case)

    def period_case():
        assert broker.requests[-1]["module"] == "kobo"
        assert broker.requests[-1]["since"] is not None
        assert broker.requests[-1]["limit"] == 50
        report["checks"]["D09_period_changes_broker_query"] = True
        screenshot("05-periode-7-jours")
        report["audits"]["D09_all_domain_source_provenance_complete"] = False
        report["audits"]["D08_long_path_text_end_to_end_consultable"] = False
        assert broker.checks == 0 and plugin.llm is None
        report["unmet_criteria"] = [
            "D08: détail complet du chemin long expurgé à vérifier",
            "D09: provenance exhaustive Core/Paris/Kobo/ADEPT/plugins avec catégories limitées",
            "D12: corrélation entre les deux identités non prouvée par broker",
        ]
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsVolumeE2E")

        def do_activate(self):
            window[0] = Gtk.ApplicationWindow(application=self)
            window[0].set_default_size(1300, 790)
            window[0].set_title("Cifereca Diagnostics UX — journal volumineux simulé")
            page[0] = plugin._build(host)
            window[0].set_child(page[0])
            window[0].present()
            until(lambda: seen("10000 disponibles"), start)

        def do_shutdown(self):
            plugin.deactivate()
            Gio.Application.do_shutdown(self)

    app = App()
    GLib.timeout_add_seconds(45, lambda: (fail("Timeout global D06-D12 45s"), False)[1] if report["status"] == "RUNNING" else False)
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="Fermeture GTK sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
