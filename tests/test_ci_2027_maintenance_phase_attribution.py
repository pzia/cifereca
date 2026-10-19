"""Closed, non-private GTK geometry phases in real Maintenance XTEST (#2027)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[1]
GTK = runpy.run_path(str(ROOT / "e2e/gtk_layout_critical.py"))
MAINT_PHASES = (
    "maint_window_start", "maint_window_shown", "maint_route",
    "maint_busy_guard", "maint_scan", "maint_first_dialog",
    "maint_cancel_stale", "maint_execute", "maint_refresh",
    "maint_p1_naming", "maint_p1_duplicates", "maint_done",
)


def test_maintenance_closed_markers_correlate_both_channels_without_secrets():
    phases = GTK["GTK_PHASES_2027"]
    assert set(MAINT_PHASES).issubset(phases)
    stdout = ("CIFERECA_E2E_GTK_PHASE:maint_window_shown\n"
              "(gtk): Gtk-CRITICAL GtkStack 0xSECRET\n"
              "CIFERECA_E2E_GTK_PHASE:maint_first_dialog\n"
              "(gtk): Gtk-CRITICAL GtkStack token=SECRET\n")
    stderr = ("CIFERECA_E2E_GTK_PHASE:maint_window_shown\n"
              "(gtk): Gtk-CRITICAL GtkRevealer 0xSECRET\n"
              "CIFERECA_E2E_GTK_PHASE:maint_first_dialog\n"
              "(gtk): Gtk-CRITICAL GtkRevealer token=SECRET\n")
    counted = GTK["gtk_layout_phase_counts"](stdout, stderr)
    assert counted == {
        "maint_window_shown": {"GtkStack": 1, "GtkRevealer": 1},
        "maint_first_dialog": {"GtkStack": 1, "GtkRevealer": 1},
    }
    assert "SECRET" not in repr(counted)


@pytest.mark.parametrize("untrusted", (
    "PRIVATE_ACCOUNT", "private-user", "maint_p1_naming/PRIVATE", "MAINT_UNKNOWN",
))
def test_unknown_marker_is_hard_boundary_even_when_malformed(untrusted):
    raw = ("CIFERECA_E2E_GTK_PHASE:maint_scan\n"
           "Gtk-CRITICAL GtkStack pointer=private\n"
           f"CIFERECA_E2E_GTK_PHASE:{untrusted}\n"
           "Gtk-CRITICAL GtkRevealer pointer=private\n")
    assert GTK["gtk_layout_phase_counts"](raw) == {
        "maint_scan": {"GtkStack": 1, "GtkRevealer": 0}
    }


def test_scenario_publishes_only_bounded_closed_labels(tmp_path):
    path = ROOT / "scripts/ci-export-e2e-summary.py"
    spec = importlib.util.spec_from_file_location("phase_export_maintenance", path)
    assert spec and spec.loader
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    source = tmp_path / "summary.json"
    item = {
        "scenario": "maintenance-tools-1366", "returncode": 0,
        "gtk_layout_phase": {"maint_first_dialog": {"GtkStack": 5, "GtkRevealer": 5}},
        "stdout": "account_token=PRIVATE_DATA", "stderr": "0xSECRET",
    }
    source.write_text(json.dumps([item]), encoding="utf-8")
    safe = exporter.safe_summary(source)["results"][0]
    assert safe["gtk_layout_phase"] == item["gtk_layout_phase"]
    assert "SECRET" not in repr(safe) and "PRIVATE" not in repr(safe)
    for phases in (
        {"maint_first_dialog_PRIVATE": {"GtkStack": 1, "GtkRevealer": 1}},
        {"maint_first_dialog": {"GtkStack": False, "GtkRevealer": 1}},
        {"maint_first_dialog": {"GtkStack": 100001, "GtkRevealer": 1}},
        {"maint_first_dialog": {"GtkStack": 1, "GtkRevealer": 1, "token": "PRIVATE"}},
    ):
        item["gtk_layout_phase"] = phases
        source.write_text(json.dumps([item]), encoding="utf-8")
        with pytest.raises(ValueError):
            exporter.safe_summary(source)


def test_real_maintenance_probe_emits_only_fixed_phase_markers_to_both_streams():
    probe = (ROOT / "e2e/maintenance_tools_probe.py").read_text()
    runner = (ROOT / "e2e/run.py").read_text()
    assert 'from gtk_layout_critical import GTK_PHASES_2027' in probe
    assert 'print(marker, file=sys.stdout, flush=True)' in probe
    assert 'print(marker, file=sys.stderr, flush=True)' in probe
    for label in MAINT_PHASES:
        assert label in probe
    assert '"gtk_layout_phase": gtk_phases if name.startswith(("mainwindow-baseline-", "maintenance-tools-")) else {}' in runner
