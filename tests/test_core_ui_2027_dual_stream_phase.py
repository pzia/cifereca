"""#2027 / #2427: independently captured GTK log streams retain phase labels."""
from __future__ import annotations

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
gtk = runpy.run_path(str(ROOT / "e2e" / "gtk_layout_critical.py"))


def test_native_warnings_in_stdout_and_stderr_are_correlated_with_same_closed_phase():
    stdout = ("CIFERECA_E2E_GTK_PHASE:window_shown\n"
              "(gtk): Gtk-CRITICAL GtkStack 0xPRIVATE POINTER\n"
              "CIFERECA_E2E_GTK_PHASE:sync_narrow\n"
              "(gtk): Gtk-CRITICAL GtkStack 0xPRIVATE SECOND\n")
    stderr = ("CIFERECA_E2E_GTK_PHASE:window_shown\n"
              "(gtk): Gtk-CRITICAL GtkRevealer 0xPRIVATE POINTER\n"
              "CIFERECA_E2E_GTK_PHASE:sync_narrow\n"
              "(gtk): Gtk-CRITICAL GtkRevealer 0xPRIVATE SECOND\n")
    safe = gtk["gtk_layout_phase_counts"](stdout, stderr)
    assert safe == {
        "window_shown": {"GtkStack": 1, "GtkRevealer": 1},
        "sync_narrow": {"GtkStack": 1, "GtkRevealer": 1},
    }
    assert "PRIVATE" not in repr(safe)


def test_unknown_markers_cannot_publish_private_phases():
    data = ("CIFERECA_E2E_GTK_PHASE:window_shown\n"
            "CIFERECA_E2E_GTK_PHASE:SECRET_PROFILE\n"
            "(gtk): Gtk-CRITICAL GtkStack https://private.invalid/?token=PRIVATE\n")
    # An unreviewed phase must not steal the previous valid phase.
    assert gtk["gtk_layout_phase_counts"](data) == {}
    assert gtk["gtk_layout_phase_counts"](None, {}) == {}


def test_probe_and_runner_use_dual_stream_without_replacing_existing_contract():
    source = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text()
    runner = (ROOT / "e2e/run.py").read_text()
    assert 'print(marker, file=sys.stdout, flush=True)' in source
    assert 'print(marker, file=sys.stderr, flush=True)' in source
    assert 'gtk_layout_phase_counts(output, errors)' in runner
    assert '"gtk_layout_phase": gtk_phases if name.startswith(("mainwindow-baseline-", "maintenance-tools-")) else {}' in runner
