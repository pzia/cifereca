"""P0 #2027: GTK dialog opening and response phases stay distinct and redacted."""
from __future__ import annotations

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
PARSER = runpy.run_path(str(ROOT / "e2e/gtk_layout_critical.py"))


def test_separates_dialog_visible_and_response_on_both_streams():
    marker = "CIFERECA_E2E_GTK_PHASE:"
    stdout = (f"{marker}maint_dialog_visible\n"
              "(gtk): Gtk-CRITICAL GtkStack PRIVATE_POINTER\n"
              f"{marker}maint_dialog_response\n"
              "(gtk): Gtk-CRITICAL GtkStack PRIVATE_POINTER\n")
    stderr = (f"{marker}maint_dialog_visible\n"
              "(gtk): Gtk-CRITICAL GtkRevealer PRIVATE_POINTER\n"
              f"{marker}maint_dialog_response\n"
              "(gtk): Gtk-CRITICAL GtkRevealer PRIVATE_POINTER\n")
    assert PARSER["gtk_layout_phase_counts"](stdout, stderr) == {
        "maint_dialog_visible": {"GtkStack": 1, "GtkRevealer": 1},
        "maint_dialog_response": {"GtkStack": 1, "GtkRevealer": 1},
    }


def test_p1_labels_closed_and_unknown_marker_ends_prior_phase():
    output = ("CIFERECA_E2E_GTK_PHASE:maint_p1_dialog_visible\n"
              "Gtk-CRITICAL GtkStack 0xPRIVATE\n"
              "CIFERECA_E2E_GTK_PHASE:untrusted_account\n"
              "Gtk-CRITICAL GtkStack 0xSECRET\n"
              "CIFERECA_E2E_GTK_PHASE:maint_p1_dialog_response\n"
              "Gtk-CRITICAL GtkRevealer 0xPRIVATE\n")
    assert PARSER["gtk_layout_phase_counts"](output) == {
        "maint_p1_dialog_visible": {"GtkStack": 1, "GtkRevealer": 0},
        "maint_p1_dialog_response": {"GtkStack": 0, "GtkRevealer": 1},
    }


def test_fixture_emits_at_dialog_response_boundary_without_weakening_xtest():
    src = (ROOT / "e2e/maintenance_tools_probe.py").read_text()
    assert 'gtk_phase_2027("maint_p1_dialog_visible" if tool.tool_id' in src
    assert 'gtk_phase_2027("maint_p1_dialog_response" if tool.tool_id' in src
    assert 'physical(response)' in src
    assert 'until(allocated, click_then_continue, timeout=6.0)' in src
    exporter = (ROOT / "scripts/ci-export-e2e-summary.py").read_text()
    for name in ("maint_dialog_visible", "maint_dialog_response",
                 "maint_p1_dialog_visible", "maint_p1_dialog_response"):
        assert name in PARSER["GTK_PHASES_2027"]
        assert f'"{name}"' in exporter
