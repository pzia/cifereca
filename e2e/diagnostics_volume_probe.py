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
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import ImageGrab
from diagnostics_page_probe import Broker, Context, Host, Tasks, walk
from x11_input import X11Input


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
            detail = {
                "path": LONG_PATH,
                "secret": "SECRET-FICTIF-NE-PAS-AFFICHER",
                # Forty harmless, locally generated entries force the real
                # 250px detail viewport to scroll; no external provider data.
                "trace_steps": [
                    f"etape-fictive-{j:03d} : information locale sans reseau"
                    for j in range(40)
                ],
            }
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
    window, page, device, long_detail = [None], [None], [None], [None]
    rc = [1]
    started = monotonic()
    d06_next_activated = [None]
    d06_filter_activated = [None]

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
                # The fixture contains synthetic, local data only. Emit a
                # fixed source position and exception class for CI diagnosis,
                # never the GTK text or the raw exception message.
                location = None
                trace = exc.__traceback__
                while trace is not None:
                    if trace.tb_frame.f_code.co_filename == __file__:
                        location = trace.tb_lineno
                    trace = trace.tb_next
                # Published CI summary accepts source-reviewed assert/raise
                # lines only. The raw fixture report remains runner-local.
                report["failure_line"] = location
                print(f"CIFERECA_DIAG_VOLUME_FAIL line={location} type={type(exc).__name__}",
                      file=sys.stderr, flush=True)
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, cb)

    def until(predicate, then, seconds=8):
        # Source-reviewed call site only; never publish GTK text/predicates.
        report["wait_line"] = sys._getframe(1).f_lineno
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

    def query_module(mod, expected, then):
        previous = len(broker.requests)
        entry = module_entry()
        entry.set_text(mod)
        entry.emit("activate")
        # The broker can return before the GTK idle handler applies the new
        # page. A generic "événements affichés" can refer to the old result.
        until(lambda: len(broker.requests) > previous
              and broker.requests[-1]["module"] == mod
              and not seen("Actualisation en cours")
              and seen(expected), then)

    def query_module_physical(mod, expected, then):
        """A real key sequence, unlike direct Gtk.Entry.set_text/emit."""
        previous = len(broker.requests)
        entry = module_entry()
        assert entry.grab_focus() is not False

        def enter_text():
            # Gtk.Entry delegates focus to its GtkText child in GTK4.
            # Test the actual focused descendant, not only entry.has_focus().
            focused = window[0].get_focus()
            assert focused is not None and (
                focused is entry or focused.is_ancestor(entry)
            ), "D06: le champ Module et ses enfants n'ont pas le focus"
            device[0].type_text(mod)
            until(lambda: entry.get_text() == mod, submit_filter, seconds=5)

        def submit_filter():
            # The module field explicitly applies on Enter; typing must NOT
            # emit one broker request per character (slow/expensive for 10k).
            assert len(broker.requests) == previous, (
                "D06: des requêtes broker ont été lancées pendant la frappe",
                len(broker.requests), previous,
            )
            report["checks"]["D06_keyboard_typing_does_not_query_each_character"] = True
            d06_filter_activated[0] = monotonic()
            device[0].key("Return", True)
            device[0].key("Return", False)
            until(
                lambda: len(broker.requests) > previous
                and broker.requests[-1]["module"] == mod
                and not seen("Actualisation en cours")
                and seen(expected),
                lambda: finish_filter(then), seconds=7,
            )

        def finish_filter(next_step):
            assert broker.requests[-1]["offset"] == 0
            assert len(widgets(Gtk.Label)) < 1200
            report["audits"]["D06_xtest_filter_gtk_seconds"] = round(
                monotonic() - d06_filter_activated[0], 3,
            )
            report["checks"]["D06_module_filter_keyboard_xtest_applies_and_resets_page"] = True
            next_step()

        later(enter_text, 190)

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
        # The read-only broker can finish before Xvfb has drawn the frame.
        # Capture the *visible 50 rows*, never the prior "loading" frame.
        later(d06_first_page_rendered, 340)

    def d06_first_page_rendered():
        assert seen("50 événements affichés") and seen("10000 disponibles")
        assert not seen("Actualisation en cours"), "D06: première page pas rendue"
        assert find_button("Suivant").get_sensitive()
        screenshot("00-10000-evenements-premiere-page")
        next_btn = find_button("Suivant")
        assert next_btn.get_sensitive()
        assert next_btn.grab_focus() is not False
        later(lambda: d06_next_keyboard(next_btn), 220)

    def d06_next_keyboard(next_btn):
        assert next_btn.has_focus(), "D06: Suivant inatteignable au clavier"
        d06_next_activated[0] = monotonic()
        device[0].key("space", True)
        device[0].key("space", False)
        until(lambda: len(broker.requests) == 2 and
              seen("page à partir de 51"), next_page)

    def next_page():
        assert broker.requests[-1]["offset"] == 50
        assert broker.generated == 100
        assert len(widgets(Gtk.Label)) < 1200
        assert seen("10000 disponibles")
        report["checks"]["D06_stable_pagination_next_50"] = True
        report["checks"]["D06_next_page_keyboard_xtest_50_rows"] = True
        report["audits"]["D06_xtest_next_page_gtk_seconds"] = round(
            monotonic() - d06_next_activated[0], 3,
        )
        later(d06_next_page_rendered, 340)

    def d06_next_page_rendered():
        assert seen("page à partir de 51") and seen("50 événements affichés")
        assert not seen("Actualisation en cours"), "D06: seconde page pas rendue"
        assert find_button("Précédent").get_sensitive()
        screenshot("01-page-51-sur-10000")
        query_module_physical("adept", "ADEPT code 10", adept_case)

    def adept_case():
        assert broker.requests[-1]["module"] == "adept"
        assert broker.requests[-1]["offset"] == 0, "Filtre module doit remettre à zéro l'offset"
        assert seen("ADEPT code 10")
        assert seen("traces libgourou absentes")
        assert not seen("Adobe refuse") and not seen("cause confirmée")
        report["checks"]["D10_adept_code10_no_invented_adobe_cause"] = True
        report["checks"]["D09_adept_module_filter"] = True
        screenshot("02-adept-code10-absence-preuve")
        query_module("paris", "paris.lending", paris_loaded)

    def paris_loaded():
        # Info-level groups are collapsed by default, unlike error groups.
        # The user must open the group before reading its event chronology.
        groups = [
            w for w in widgets(Gtk.Expander)
            if (w.get_label() or "").startswith("paris.lending · ")
        ]
        assert len(groups) == 2, ("groupes de provenance Paris", len(groups))
        for group in groups:
            group.set_expanded(True)
        until(lambda: seen("Paris retour demandé et accepté")
              and seen("profil-pseudo-A") and seen("profil-pseudo-B"), paris_case)

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
        # The public event provenance separates the two synthetic profiles,
        # but does not prove a cross-profile loan join. Never infer it.
        assert pseudo_A and pseudo_B, "Les profils pseudonymes ne sont pas séparés"
        sources = {
            group.get_label().split(" · ")[1]
            for group in widgets(Gtk.Expander)
            if (group.get_label() or "").startswith("paris.lending · ")
        }
        assert sources == {"app_log", "core_events"}, sources
        origin = [row for row in labels() if row.startswith("Source : ")]
        assert any("Source : core_events · Module : paris.lending" in row for row in origin)
        assert any("Source : app_log · Module : paris.lending" in row for row in origin)
        assert any("Référence : ux-evt-00006" in row for row in origin)
        assert any("Référence : ux-evt-00011" in row for row in origin)
        report["checks"]["D09_event_provenance_source_module_reference_visible"] = True
        report["checks"]["D12_pseudonymous_profiles_displayed_separately"] = True
        screenshot("03-paris-retour-profils")
        query_module("kobo", "Erreur fictive longue", kobo_case)

    def kobo_case():
        assert broker.requests[-1]["module"] == "kobo"
        assert seen("Erreur fictive longue")
        assert len(widgets(Gtk.Label)) < 1200
        visible = " ".join(labels())
        assert LONG_PATH not in visible, "Chemin privé complet visible dans liste"
        report["checks"]["D08_long_path_redacted_list_and_stable_layout"] = True
        # The data has loaded but GTK must still paint the frame: do not
        # photograph the transient "Actualisation en cours" state.
        later(kobo_details_stage, 230)

    def kobo_details_stage():
        screenshot("04-kobo-erreur-chemin-long")
        # The GTK projection includes bounded, redacted detail. Inspect the
        # rendered TextView and verify both useful fields and absent secrets.
        # Target specifically the *long error* (event 00002), not the
        # first generic Kobo row sorted newest-first in the same group.
        error_labels = [
            w for w in widgets(Gtk.Label)
            if "Erreur fictive longue" in (w.get_text() or "")
        ]
        assert len(error_labels) == 1, ("Erreur Kobo ciblée", len(error_labels))
        item = error_labels[0].get_parent().get_parent()
        panes = [
            w for w in walk(item)
            if isinstance(w, Gtk.Expander) and w.get_label() == "Détails expurgés"
        ]
        assert len(panes) == 1, "Détail de l'erreur Kobo introuvable"
        panes[0].set_expanded(True)
        views = [w for w in walk(panes[0]) if isinstance(w, Gtk.TextView)]
        assert len(views) == 1
        buffer = views[0].get_buffer()
        detail_text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        assert "event_id:" in detail_text and "message:" in detail_text
        assert LONG_PATH not in detail_text
        report["audits"]["D08_raw_detail_field_preserved"] = "detail:" in detail_text
        assert report["audits"]["D08_raw_detail_field_preserved"]
        # Report ONLY test-safe presence flags, not GTK text or private data.
        expected = {
            "path_visible_bounded": '/home/fixture/secret/' in detail_text,
            "secret_visible": "SECRET-FICTIF-NE-PAS-AFFICHER" in detail_text,
            "trace_first": "etape-fictive-000" in detail_text,
            "trace_last": "etape-fictive-039" in detail_text,
            "bounded": len(detail_text) < 24000,
        }
        report["audits"]["D08_long_detail_safe_presence_flags"] = expected
        missing = [key for key, present in expected.items() if not present]
        assert not missing, "D08: segments absents ou non expurges: " + ", ".join(missing)
        # Gtk.DropDown may contain its own internal ScrolledWindow. Resolve
        # the scroll ancestor of the exact rendered TextView instead of
        # assuming a single scroller exists in the complete expander tree.
        detail_scroll = views[0].get_parent()
        while detail_scroll is not None and not isinstance(detail_scroll, Gtk.ScrolledWindow):
            detail_scroll = detail_scroll.get_parent()
        assert detail_scroll is not None, "D08: scroll interne du texte absent"
        assert detail_scroll.get_child() is views[0], "D08: mauvais conteneur de texte"
        report["audits"]["D08_scroll_belongs_to_visible_textview"] = True
        long_detail[0] = (views[0], detail_scroll, detail_text)
        report["audits"]["D08_complete_redacted_long_detail_available"] = True
        report["checks"]["D08_real_detail_panel_available_and_redacted"] = True
        journal_scroll = error_labels[0].get_parent()
        while journal_scroll is not None and not isinstance(journal_scroll, Gtk.ScrolledWindow):
            journal_scroll = journal_scroll.get_parent()
        assert journal_scroll is not None
        ok, rect = error_labels[0].compute_bounds(journal_scroll.get_child())
        assert ok
        adjustment = journal_scroll.get_vadjustment()
        target = max(0, min(
            float(rect.get_y()) - 45,
            adjustment.get_upper() - adjustment.get_page_size(),
        ))
        adjustment.set_value(target)
        report["audits"]["D08_long_error_in_view_scroll_y"] = round(target, 2)
        later(kobo_detail_visible, 260)

    def kobo_detail_visible():
        view, scroller, detail_text = long_detail[0]
        assert view.get_mapped(), "D08 TextView GTK non monte"
        adjustment = scroller.get_vadjustment()
        maximum = adjustment.get_upper() - adjustment.get_page_size()
        assert maximum > 80, ("D08: detail non defilable", maximum)
        adjustment.set_value(0)
        assert view.grab_focus(), "D08: detail inaccessible au clavier"
        assert window[0].get_focus() is view, "D08: focus TextView perdu"
        report["audits"]["D08_scroll_range_px"] = round(maximum, 2)
        screenshot("04b-kobo-detail-rendered")
        # Physical XTEST keyboard navigation, not an adjustment fabricated
        # by the probe to claim that the end of the long detail is readable.
        query_count = len(broker.requests)
        device[0].key("Control_L", True)
        device[0].key("End", True)
        device[0].key("End", False)
        device[0].key("Control_L", False)
        # Ctrl+End was sent physically via XTEST above. GTK applies its
        # resulting scroll asynchronously: a fixed 260ms snapshot sometimes
        # observed the previous position (CI diagnostics-volume-1366).
        # Wait only for the *real* value to reach the actual content end;
        # never fabricate success with adjustment.set_value(bottom).
        until(
            lambda: adjustment.get_value() >= max(
                0, adjustment.get_upper() - adjustment.get_page_size()
            ) - 5,
            lambda: verify_kobo_detail_keyboard_end(
                adjustment, detail_text, query_count,
            ),
            seconds=3,
        )

    def verify_kobo_detail_keyboard_end(adjustment, detail_text, query_count):
        bottom = max(0, adjustment.get_upper() - adjustment.get_page_size())
        assert bottom > 80, ("D08: contenu trop court", bottom)
        assert adjustment.get_value() >= bottom - 5, (
            "D08: Ctrl+Fin XTEST n'atteint pas la fin du detail",
            round(adjustment.get_value(), 2), round(bottom, 2),
        )
        assert "etape-fictive-039" in detail_text
        assert len(broker.requests) == query_count, "La lecture ne doit pas relancer de consultation"
        report["audits"]["D08_long_path_text_end_to_end_consultable"] = True
        report["checks"]["D08_long_detail_keyboard_end_xtest"] = True
        report["audits"]["D08_keyboard_end_scroll_px"] = round(adjustment.get_value(), 2)
        screenshot("04c-kobo-detail-bottom-xtest")
        # Filter period is really sent as a bounded UTC 'since' query.
        selectors = [w for w in widgets(Gtk.DropDown) if w.get_tooltip_text() == "Période de la consultation"]
        assert len(selectors) == 1
        before = len(broker.requests)
        selectors[0].set_selected(3)  # 7 jours
        until(lambda: len(broker.requests) > before
              and broker.requests[-1]["since"] is not None
              and not seen("Actualisation en cours")
              and seen("Erreur fictive longue"), period_case)

    def period_case():
        assert broker.requests[-1]["module"] == "kobo"
        assert broker.requests[-1]["since"] is not None
        assert broker.requests[-1]["limit"] == 50
        report["checks"]["D09_period_changes_broker_query"] = True
        screenshot("05-periode-7-jours")
        report["audits"]["D09_all_domain_source_provenance_complete"] = False
        assert report["audits"]["D08_long_path_text_end_to_end_consultable"] is True
        # Only two PUBLIC broker source categories exist: core_events and
        # app_log. A provider module such as Kobo is not a third source.
        # Unlike set_selected(), keyboard XTEST exercises the accessible GTK
        # DropDown itself, including selection, asynchronous broker query and
        # visible provenance after the popup closes.
        dropdowns = [
            w for w in widgets(Gtk.DropDown)
            if w.get_tooltip_text() == "Source des événements"
        ]
        assert len(dropdowns) == 1
        choose_source_keyboard(dropdowns[0], ("End",), 2, "app_log", source_filtered)

    def choose_source_keyboard(dropdown, keys, selected, source, next_step):
        previous = len(broker.requests)
        assert dropdown.grab_focus() is not False, "D09: filtre Source sans focus"

        def press_dropdown():
            # Gtk.DropDown delegates keyboard focus to its internal toggle
            # child. The focus-ring is visible, but dropdown.has_focus()
            # itself can be False despite valid keyboard ownership.
            focused = window[0].get_focus()
            assert focused is not None and (
                focused is dropdown or focused.is_ancestor(dropdown)
            ), "D09: le choix Source et ses enfants n'ont pas le focus"
            for keysym in ("space", *keys, "Return"):
                device[0].key(keysym, True)
                device[0].key(keysym, False)
            until(
                lambda: dropdown.get_selected() == selected
                and len(broker.requests) > previous
                and broker.requests[-1]["source"] == source
                and not seen("Actualisation en cours")
                and seen("kobo.catalog"),
                next_step,
            )
        later(press_dropdown, 240)

    def source_filtered():
        assert broker.requests[-1]["module"] == "kobo"
        assert broker.requests[-1]["source"] == "app_log"
        groups = [
            w.get_label() or "" for w in widgets(Gtk.Expander)
            if (w.get_label() or "").startswith("kobo.catalog · ")
        ]
        assert groups and all("· app_log ·" in row for row in groups), groups
        assert not any("· core_events ·" in row for row in groups)
        assert seen("Journal partiel"), "D09: rotation du journal masquée"
        report["checks"]["D09_source_filter_no_cross_source_attribution"] = True
        report["checks"]["D09_app_log_source_selected_by_real_keyboard_xtest"] = True
        # The async broker has updated GTK widgets, but Xvfb still needs a
        # compositor frame before ImageGrab reflects the finished page.
        later(source_app_log_rendered, 320)

    def source_app_log_rendered():
        assert seen("Journal partiel") and not seen("Actualisation en cours")
        assert any((w.get_label() or "").startswith("kobo.catalog · app_log")
                   for w in widgets(Gtk.Expander))
        screenshot("06-filtre-source-journal-applicatif")
        src = [
            w for w in widgets(Gtk.DropDown)
            if w.get_tooltip_text() == "Source des événements"
        ][0]
        choose_source_keyboard(src, ("End", "Up"), 1, "core_events", source_core_filtered)

    def source_core_filtered():
        assert broker.requests[-1]["module"] == "kobo"
        assert broker.requests[-1]["source"] == "core_events"
        groups = [
            w.get_label() or "" for w in widgets(Gtk.Expander)
            if (w.get_label() or "").startswith("kobo.catalog · ")
        ]
        assert groups and all("· core_events ·" in row for row in groups), groups
        assert not any("· app_log ·" in row for row in groups)
        report["checks"]["D09_core_events_source_selected_by_real_keyboard_xtest"] = True
        report["checks"]["D09_both_sources_have_distinct_visible_provenance"] = True
        later(source_core_rendered, 320)

    def source_core_rendered():
        assert not seen("Actualisation en cours")
        assert any((w.get_label() or "").startswith("kobo.catalog · core_events")
                   for w in widgets(Gtk.Expander))
        screenshot("06b-filtre-source-evenements-core")
        src = [
            w for w in widgets(Gtk.DropDown)
            if w.get_tooltip_text() == "Source des événements"
        ][0]
        choose_source_keyboard(
            src, ("Home",), 0, "all",
            lambda: query_module("paris", "paris.lending", paris_copy_loaded),
        )

    def paris_copy_loaded():
        groups = [
            w for w in widgets(Gtk.Expander)
            if (w.get_label() or "").startswith("paris.lending · ")
        ]
        assert len(groups) == 2, ("groupes Paris", len(groups))
        for group in groups:
            group.set_expanded(True)
        until(
            lambda: seen("profil-pseudo-A") and seen("profil-pseudo-B"),
            copy_one_profile,
        )

    def copy_one_profile():
        # Select the actual row for profile A in the GTK event list.
        rows = [
            w for w in widgets(Gtk.Label)
            if "profil-pseudo-A" in (w.get_text() or "")
        ]
        assert len(rows) == 1, len(rows)
        line = rows[0].get_parent()
        checks = [w for w in walk(line) if isinstance(w, Gtk.CheckButton)]
        assert len(checks) == 1
        checks[0].set_active(True)
        # Make the selected profile visible inside the journals viewport for
        # screenshot evidence. The clipboard assertion remains independent.
        scroller = rows[0].get_parent()
        while scroller is not None and not isinstance(scroller, Gtk.ScrolledWindow):
            scroller = scroller.get_parent()
        assert scroller is not None, "Conteneur GTK des journaux introuvable"
        ok, rect = rows[0].compute_bounds(scroller.get_child())
        assert ok, "Position du profil A introuvable"
        adjustment = scroller.get_vadjustment()
        scroll_to = max(0, min(
            float(rect.get_y()) - 30,
            adjustment.get_upper() - adjustment.get_page_size(),
        ))
        adjustment.set_value(scroll_to)
        report["audits"]["D12_profileA_in_view_scroll_y"] = round(scroll_to, 2)
        btn = find_button("Copier la sélection")
        assert btn.get_sensitive()
        btn.emit("clicked")
        GLib.timeout_add(240, read_copy)

    def read_copy():
        def done(clipboard, task):
            try:
                copied = clipboard.read_text_finish(task)
                payload = json.loads(copied)
                assert len(payload["events"]) == 1
                event = payload["events"][0]
                assert event["event_id"] == "ux-evt-00006", event["event_id"]
                assert event["source"] == "core_events"
                assert event["module"] == "paris.lending"
                assert "profil-pseudo-A" in event["message"]
                assert "profil-pseudo-B" not in copied
                assert "SECRET-FICTIF" not in copied
                report["checks"]["D12_copy_one_identity_without_mixing_profiles"] = True
                report["audits"]["D12_broker_link_between_profiles_proven"] = False
                screenshot("07-copie-un-seul-profil")
                finish()
            except Exception as exc:
                fail(f"Copie identité : {type(exc).__name__}: {exc}")
        Gdk.Display.get_default().get_clipboard().read_text_async(None, done)
        return False

    def finish():
        assert broker.checks == 0 and plugin.llm is None
        report["unmet_criteria"] = [
            "D06: broker virtuel 10000 entrées, performance sur journaux réels et matériel non attestée",
            "D09: seules catégories Core/app_log disponibles, provenance métier par module uniquement",
            "D12: aucune corrélation inter-identités attestée par broker ; copie unitaire isolée seulement",
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
            device[0] = X11Input()
            until(lambda: seen("10000 disponibles"), start)

        def do_shutdown(self):
            if device[0] is not None:
                device[0].close()
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
