"""P0 #2565: end-to-end redacted reconciliation phase progress.

The real Core broker emits closed phase messages. The GTK progress component
must show these without inventing percentages, revealing filenames or changing
existing maintenance action behavior. All filesystem inputs are synthetic.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress
from tests.test_library_audit_broker import scoped


def _line():
    pending, rendered = [], []
    line = MaintenanceLineProgress(
        idle_add=pending.append, render=rendered.append,
        activity_label="Réconciliation",
    )
    ticket = line.begin()
    line.confirmation(ticket)
    line.running(ticket)
    pending.pop(0)()
    return line, ticket, pending, rendered


def test_real_broker_emits_closed_progress_without_private_identifiers(scoped, tmp_path):
    service, _, _ = scoped
    events = []
    control = SimpleNamespace(cancelled=lambda: False, report=lambda **kw: events.append(kw))
    report = LibraryReconciliationReadBrokerImpl(service).audit(
        identity_id="alice", control=control,
    )
    assert report["status"] == "complete"
    phases = [event["message"] for event in events]
    for phase in (
        "Lecture des preuves locales", "Inventaire local",
        "Comparaison des preuves locales",
        "Comparaison des identifiants bibliographiques",
        "Vérification des chemins enregistrés", "Audit local terminé",
    ):
        assert phase in phases
    assert any(event.get("current") == 1 and event.get("total") is None
               and event.get("message") == "Inventaire local" for event in events)
    encoded = json.dumps(events, ensure_ascii=False)
    assert "PRIVATE" not in encoded
    assert str(tmp_path) not in encoded
    assert "alice" not in encoded


def test_all_broker_phases_render_safely_without_pretending_to_be_percentages(scoped):
    service, _, _ = scoped
    line, ticket, pending, rendered = _line()
    control = SimpleNamespace(
        cancelled=lambda: False,
        report=lambda **kw: line.report(ticket, **kw),
    )
    assert LibraryReconciliationReadBrokerImpl(service).audit(
        identity_id="alice", control=control)["status"] == "complete"
    pending.pop(0)()
    assert rendered[-1].label == "Réconciliation · Audit local terminé…"
    assert rendered[-1].fraction is None
    assert line.state.phase == "running"
    line.finish(ticket, "success")
    pending.pop(0)()
    assert rendered[-1].label == "Terminé · 100 %"
    assert rendered[-1].fraction == 1.0


def test_inventory_counter_and_other_closed_stages_are_useful_without_total():
    line, ticket, pending, rendered = _line()
    line.report(ticket, current=517, total=None, message="Inventaire local")
    pending.pop(0)()
    assert rendered[-1].label == "Réconciliation · Inventaire local · 517 fichier(s)"
    assert rendered[-1].fraction is None
    line.report(ticket, current=None, total=None, message="Lecture des preuves locales")
    pending.pop(0)()
    assert rendered[-1].label == "Réconciliation · Lecture des preuves locales…"
    assert rendered[-1].fraction is None


def test_untrusted_progress_is_not_rendered_or_allowed_to_replace_safe_stage():
    line, ticket, pending, rendered = _line()
    line.report(ticket, current=0, total=None, message="Inventaire local")
    pending.pop(0)()
    expected = rendered[-1].label
    for value in ("/home/PRIVATE/account/token", ["Inventaire local"],
                  "Inventaire local\nPRIVATE", "unknown_stage", None):
        line.report(ticket, current=10**10, total=None, message=value)
    assert pending == []
    assert line.state.label == expected
    line.report(ticket, current=True, total=None, message="Inventaire local")
    pending.pop(0)()
    assert "True" not in rendered[-1].label
    assert rendered[-1].fraction is None
    line.report(ticket, current=517, total=None, message="PRIVATE_TOKEN")
    assert "PRIVATE_TOKEN" not in line.state.label


def test_reconciliation_never_accepts_a_plugin_reported_total_as_percent():
    line, ticket, pending, rendered = _line()
    line.report(ticket, current=517, total=517, message="Inventaire local")
    pending.pop(0)()
    assert rendered[-1].label == "Réconciliation · Inventaire local…"
    assert rendered[-1].fraction is None
    line.report(ticket, current=9999, total=10000, message="Audit local terminé")
    pending.pop(0)()
    assert rendered[-1].label == "Réconciliation · Audit local terminé…"
    assert rendered[-1].fraction is None
    line.report(ticket, current=1, total=1, message="/PRIVATE/SECRET")
    assert pending == []
    assert line.state.fraction is None
    assert "/PRIVATE" not in line.state.label


def test_other_maintenance_tools_retain_bounded_known_total_progress():
    pending, rendered = [], []
    line = MaintenanceLineProgress(
        idle_add=pending.append, render=rendered.append,
        activity_label="Purge",
    )
    ticket = line.begin()
    line.confirmation(ticket)
    line.running(ticket)
    pending.pop(0)()
    line.report(ticket, current=2, total=5, message="some irrelevant plugin text")
    pending.pop(0)()
    assert rendered[-1].label == "Purge en cours · 40 %"
    assert rendered[-1].fraction == 0.4
    line.report(ticket, current=5, total=5, message="done")
    pending.pop(0)()
    assert rendered[-1].label == "Purge en cours · 99 %"
    assert rendered[-1].fraction == 0.99


def test_cancelled_or_failed_job_never_claims_hundred_percent():
    line, ticket, pending, rendered = _line()
    line.report(ticket, current=517, total=None, message="Inventaire local")
    line.finish(ticket, "error")
    pending.pop(0)()
    assert rendered[-1].phase == "error"
    assert rendered[-1].fraction is None
    line.report(ticket, current=999, total=None, message="Audit local terminé")
    assert pending == []
    assert line.state.phase == "error"
