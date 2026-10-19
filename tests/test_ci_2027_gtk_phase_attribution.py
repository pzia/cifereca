"""#2027: privacy-bounded phase attribution for real GTK E2E."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_ci_2027_gtk_layout_critical import MODULE as GTK_MODULE

ROOT = Path(__file__).resolve().parents[1]


def _phase_parser():
    import runpy
    return runpy.run_path(str(GTK_MODULE))["gtk_layout_phase_counts"]


def test_real_gtk_warning_count_is_attributed_only_to_fixed_phases():
    parser = _phase_parser()
    stderr = "\n".join((
        "Gtk-CRITICAL GtkStack 0xPRIVATE initial",
        "CIFERECA_E2E_GTK_PHASE:window_start",
        "Gtk-CRITICAL GtkStack 0xSECRET_ADDRESS",
        "Gtk-CRITICAL GtkRevealer 0xSECRET_ADDRESS",
        "CIFERECA_E2E_GTK_PHASE:sync_font_narrow",
        "Gtk-CRITICAL GtkStack 0xSECRET_ADDRESS",
        "Gtk-WARNING GtkRevealer not critical",
        "Gtk-CRITICAL GtkRevealer 0xSECRET_ADDRESS",
        "CIFERECA_E2E_GTK_PHASE:done",
        "Gtk-CRITICAL GtkStack 0xSECRET_ADDRESS",
    ))
    summary = parser(stderr)
    assert summary == {
        "window_start": {"GtkStack": 1, "GtkRevealer": 1},
        "sync_font_narrow": {"GtkStack": 1, "GtkRevealer": 1},
        "done": {"GtkStack": 1, "GtkRevealer": 0},
    }
    assert "SECRET" not in repr(summary) and "PRIVATE" not in repr(summary)


def test_unknown_phase_and_provider_output_never_reach_public_result():
    parser = _phase_parser()
    raw = "\n".join((
        "CIFERECA_E2E_GTK_PHASE:PRIVATE_ACCOUNT",
        "Gtk-CRITICAL GtkStack token=PRIVATE",
        "CIFERECA_E2E_GTK_PHASE:window_shown",
        "Gtk-CRITICAL GtkStack 0xPRIVATE",
        "CIFERECA_E2E_GTK_PHASE:evil-custom",
        "Gtk-CRITICAL GtkRevealer 0xPRIVATE",
    ))
    assert parser(raw) == {"window_shown": {"GtkStack": 1, "GtkRevealer": 1}}
    assert parser(None) == {}
    assert parser("Gtk-CRITICAL GtkStack 0xPRIVATE") == {}


def test_ci_export_drops_unknown_fields_and_rejects_unreviewed_phase_data(tmp_path):
    path = ROOT / "scripts/ci-export-e2e-summary.py"
    spec = importlib.util.spec_from_file_location("cifereca_2027_phase_export", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "summary.json"
    row = {
        "scenario": "mainwindow-baseline-1366", "returncode": 0,
        "gtk_layout_phase": {
            "sync_font_narrow": {"GtkStack": 3, "GtkRevealer": 3},
        },
        "stderr": "GtkStack at 0xSECRET with signed URL",
    }
    source.write_text(json.dumps([row]))
    safe = module.safe_summary(source)["results"][0]
    assert safe["gtk_layout_phase"] == row["gtk_layout_phase"]
    assert "SECRET" not in repr(safe)
    for invalid in (
        {"PRIVATE_TOKEN": {"GtkStack": 1, "GtkRevealer": 1}},
        {"sync_font_narrow": {"GtkStack": "1", "GtkRevealer": 1}},
        {"sync_font_narrow": {"GtkStack": 1, "GtkRevealer": True}},
        {"sync_font_narrow": {"GtkStack": -1, "GtkRevealer": 1}},
        {"sync_font_narrow": {"GtkStack": 1, "GtkRevealer": 1, "url": "PRIVATE"}},
    ):
        row["gtk_layout_phase"] = invalid
        source.write_text(json.dumps([row]))
        with pytest.raises(ValueError):
            module.safe_summary(source)


def test_real_diagnostics_baseline_contains_all_source_reviewed_phase_markers():
    probe = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text()
    markers = (
        "window_start", "window_shown", "sync_desktop",
        "sync_font_desktop", "sync_narrow", "sync_font_narrow",
        "sync_restored", "numilog_consent", "done",
    )
    assert 'from gtk_layout_critical import GTK_PHASES_2027' in probe
    for phase in markers:
        assert f'gtk_phase_2027("{phase}")' in probe or (
            phase in ("sync_desktop", "sync_font_desktop")
            and '"sync_desktop" if not large_font else "sync_font_desktop"' in probe
        )
