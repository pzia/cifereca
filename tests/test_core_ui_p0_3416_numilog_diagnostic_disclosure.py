"""#3416: readable Numilog source card without losing account diagnostics."""
from __future__ import annotations

from pathlib import Path

from cifereca.gui.numilog_materialization_status import (
    describe_run, compact_run_caption,
)


def _detailed_status():
    detail = {
        "phase": "completed", "cause_phase": "preflight",
        "code": "preflight_failed", "status": "blocked",
        "task_kind": "source-materialize", "attempted": 0,
        "downloaded": 0, "skipped": 3, "failed": 0,
        "item_decisions": [
            {"position": 1, "decision": "needs_reconciliation"},
            {"position": 2, "decision": "purchase_unproven"},
            {"position": 3, "decision": "source_not_ready"},
        ],
    }
    return {"status": "blocked", "attempted": 0, "downloaded": 0,
            "skipped": 3, "failed": 0, "diagnostic": detail}


def test_compact_source_card_keeps_terminal_state_and_counters():
    full = describe_run("manual", _detailed_status())
    brief = compact_run_caption(full)
    assert "Dernier essai bloqué" in brief
    assert "0 téléchargé(s)" in brief and "0 échec(s)" in brief
    assert "préflight" not in brief and "preflight_failed" not in brief
    assert "n°1" not in brief and "n°2" not in brief
    assert "n°1" in full and "preflight_failed" in full
    assert len(brief) <= 180


def test_never_run_is_not_a_failure_and_empty_mode_is_empty():
    never = {"status": "never", "attempted": 0, "downloaded": 0,
             "skipped": 0, "failed": 0}
    full = describe_run("manual", never)
    assert compact_run_caption(full) == full
    assert "Aucun essai" in full
    assert compact_run_caption(describe_run("disabled", never)) == ""


def test_header_cannot_expose_arbitrarily_long_unvalidated_input():
    assert compact_run_caption("X" * 5000) == "Dernier bilan indisponible."
    assert len(compact_run_caption("Dernier essai : " + "un mot " * 100)) <= 180


def test_sync_gtk_source_uses_collapsed_diagnostic_disclosure():
    src = Path("src/cifereca/gui/sync_controller.py").read_text(encoding="utf-8")
    assert "compact_run_caption(consent_history)" in src
    assert "diagnostic_label = Gtk.Label(label=consent_history" in src
    assert "run_details.set_expanded(False)" in src
    assert "run_details.set_child(diagnostic_label)" in src
    assert "diagnostic_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)" in src
    assert "diagnostic_label.set_selectable(True)" in src
    assert "group.add(run_details)" in src
    assert "consent_subtitle + (\" \" + consent_history" not in src
