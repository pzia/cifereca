"""#2027: phase-bounded, secret-free attribution of native GTK warnings."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
module = runpy.run_path(str(ROOT / "e2e" / "gtk_layout_critical.py"))
phase_counts = module["gtk_layout_phase_counts"]


def test_only_exact_reviewed_markers_define_gtk_incident_phase():
    stderr = "\n".join((
        "(app): Gtk-CRITICAL GtkStack before shell",
        "CIFERECA_E2E_GTK_PHASE:shell_ready",
        "(app): Gtk-CRITICAL GtkStack 0xPRIVATE 940 vs 948",
        "(app): Gtk-CRITICAL GtkRevealer 0xPRIVATE 53 vs 61",
        "CIFERECA_E2E_GTK_PHASE:PRIVATE_TOKEN",
        "(app): Gtk-CRITICAL GtkStack 0xPRIVATE",
        "CIFERECA_E2E_GTK_PHASE:sync_narrow",
        "(app): Gtk-CRITICAL GtkRevealer https://PRIVATE.invalid/?token=SECRET",
        "Gtk-CRITICAL GtkEntry unrelated",
    ))
    safe = phase_counts(stderr)
    assert safe == {
        "pre_phase": {"GtkStack": 1, "GtkRevealer": 0},
        "shell_ready": {"GtkStack": 2, "GtkRevealer": 1},
        "sync_narrow": {"GtkStack": 0, "GtkRevealer": 1},
    }
    assert "PRIVATE" not in repr(safe) and "SECRET" not in repr(safe)


def test_phase_parser_limits_input_and_does_not_invent_stages():
    assert phase_counts(None) == {}
    assert phase_counts("") == {}
    assert phase_counts("CIFERECA_E2E_GTK_PHASE:maintenance_begin\n") == {}
    assert phase_counts("prefix CIFERECA_E2E_GTK_PHASE:maintenance_begin\n"
                        "Gtk-CRITICAL GtkRevealer") == {
        "pre_phase": {"GtkStack": 0, "GtkRevealer": 1}
    }


def _exporter():
    spec = importlib.util.spec_from_file_location(
        "ci_e2e_phase_export", ROOT / "scripts" / "ci-export-e2e-summary.py"
    )
    assert spec and spec.loader
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_phase_export_refuses_arbitrary_keys_and_nested_secrets(tmp_path):
    exporter = _exporter()
    source = tmp_path / "summary.json"
    base = {"scenario": "mainwindow-baseline-1366", "returncode": 0}
    def inspect(phases, *, scenario="mainwindow-baseline-1366"):
        source.write_text(json.dumps([{
            **base, "scenario": scenario, "gtk_layout_phases": phases,
            "traceback": "PRIVATE_TOKEN",
        }]))
        return exporter.safe_summary(source)["results"][0]
    approved = {"sync_narrow": {"GtkStack": 5, "GtkRevealer": 5}}
    record = inspect(approved)
    assert record["gtk_layout_phases"] == approved
    assert "PRIVATE_TOKEN" not in repr(record)
    for bad in (
        {"PRIVATE_USERNAME": {"GtkStack": 1, "GtkRevealer": 1}},
        {"sync_narrow": {"GtkStack": True, "GtkRevealer": 0}},
        {"sync_narrow": {"GtkStack": 0, "GtkRevealer": 0, "url": "SECRET"}},
        {"sync_narrow": {"GtkStack": -1, "GtkRevealer": 2}},
        {"sync_narrow": ["PRIVATE"]},
    ):
        with pytest.raises(ValueError):
            inspect(bad)
    with pytest.raises(ValueError):
        inspect(approved, scenario="startup")


def test_e2e_phase_markers_are_in_both_real_gtk_probes_and_parsed_in_runner():
    nav = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text()
    maintenance = (ROOT / "e2e/maintenance_tools_probe.py").read_text()
    runner = (ROOT / "e2e/run.py").read_text()
    for name in ("shell_ready", "sync_narrow", "numilog_consent", "numilog_dialog"):
        assert f"CIFERECA_E2E_GTK_PHASE:{name}" in nav
    for name in ("maintenance_begin", "maintenance_preview",
                 "maintenance_execute", "maintenance_finish"):
        assert f"CIFERECA_E2E_GTK_PHASE:{name}" in maintenance
    assert "gtk_layout_phase_counts(output, errors)" in runner
    assert '"gtk_layout_phases": gtk_phases' in runner


def test_independent_stdout_and_stderr_markers_are_attributed_without_cross_contamination():
    stdout = ("CIFERECA_E2E_GTK_PHASE:maintenance_preview\n"
              "(app): Gtk-CRITICAL GtkStack 0xPRIVATE_A\n")
    stderr = ("CIFERECA_E2E_GTK_PHASE:maintenance_execute\n"
              "(app): Gtk-CRITICAL GtkRevealer 0xPRIVATE_B\n")
    assert phase_counts(stdout, stderr) == {
        "maintenance_preview": {"GtkStack": 1, "GtkRevealer": 0},
        "maintenance_execute": {"GtkStack": 0, "GtkRevealer": 1},
    }
