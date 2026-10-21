"""P0 #2027: Core E2E never reports a GTK layout storm as a pass."""
from __future__ import annotations

import runpy
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "e2e/gtk_layout_critical.py"
parser = runpy.run_path(str(MODULE))
count = parser["gtk_layout_critical_counts"]
failure = parser["gtk_layout_critical_failure"]


def test_two_affected_gtk_families_cannot_silently_pass():
    logs = "\n".join((
        "(cifereca:17): Gtk-CRITICAL **: GtkStack 0xABC reported minimum height 940 vs 948",
        "(cifereca:17): Gtk-CRITICAL **: GtkRevealer 0xDEF reported minimum height 53 vs 61",
        "(cifereca:17): Gtk-CRITICAL **: GtkStack 0xABC reported minimum height 940 vs 948",
    ))
    found = count(logs)
    assert found == {"GtkStack": 2, "GtkRevealer": 1, "stream_truncated": False}
    assert failure(found) == "GTK layout P0 #2027 : GtkStack=2, GtkRevealer=1"


def test_only_closed_categories_are_published_never_raw_log():
    secret = "https://host.invalid/?token=PRIVATE_ACCOUNT"
    output = ("Gtk-CRITICAL GtkStack 0xPRIVATE_PTR " + secret + "\n")
    result = count(output)
    assert "PRIVATE_ACCOUNT" not in repr(result)
    assert "PRIVATE_PTR" not in repr(result)
    assert "PRIVATE_ACCOUNT" not in failure(result)
    assert "PRIVATE_PTR" not in failure(result)


def test_unrelated_gtk_critical_and_noncritical_stack_do_not_fail():
    results = count(
        "GtkStack title private account\n"
        "Gtk-WARNING GtkRevealer not an allocation critical\n"
        "Gtk-CRITICAL GtkEntry a different problem\n"
    )
    assert results["GtkStack"] == results["GtkRevealer"] == 0
    assert failure(results) is None


def test_truncated_log_is_not_false_proof_of_no_critical():
    output = "normal stream\n" + "X" * 1_000_000
    results = count(output)
    assert results["stream_truncated"] is True
    assert "tronqué" in failure(results)


def test_closed_machine_aliases_and_type_validation():
    results = count("Gtk-CRITICAL gtk_stack size invalid\n"
                    "Gtk-CRITICAL gtk_revealer size invalid\n")
    assert results["GtkStack"] == results["GtkRevealer"] == 1
    assert failure({"GtkStack": "0", "GtkRevealer": 0}) == (
        "GTK layout critical counters invalides"
    )


def test_e2e_runner_attaches_counter_to_trace_and_summary_without_masking_warnings():
    source = (MODULE.parent / "run.py").read_text()
    assert "gtk_layout_critical_counts(output, errors)" in source
    assert "layout_problem = gtk_layout_critical_failure(gtk_critical)" in source
    assert 'gtk_layout_critical_observed' in source
    assert '"gtk_layout_critical": gtk_critical' in source
    assert "gtk_layout_critical=gtk_critical" in source
    assert "if returncode == 0 and layout_problem:" not in source
    assert "_write_redacted_log(stderr_path, errors, env)" in source


def test_runtime_trace_only_emits_closed_widget_counters(tmp_path):
    import importlib.util
    import json

    path = MODULE.parent / "run.py"
    spec = importlib.util.spec_from_file_location("e2e_runtime_guard_2027", path)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    output = tmp_path / "trace.jsonl"
    runner._event(
        output, "startup", "gtk_layout_critical_observed",
        gtk_layout_critical={
            "GtkStack": 5, "GtkRevealer": 5, "stream_truncated": False,
        },
        account="PRIVATE_ACSM_TOKEN",
        path="/home/private",
    )
    raw = output.read_text()
    event = json.loads(raw)
    assert event["gtk_layout_critical"] == {
        "GtkStack": 5, "GtkRevealer": 5, "stream_truncated": False,
    }
    assert "PRIVATE" not in raw and "/home/" not in raw
    with __import__("pytest").raises(ValueError):
        runner._event(
            output, "startup", "gtk_layout_critical_observed",
            gtk_layout_critical={
                "GtkStack": 5, "GtkRevealer": 5,
                "stream_truncated": False, "screenshot": "PRIVATE",
            },
        )
    assert output.read_text() == raw


def test_post_fix_geometry_gate_is_closed_and_only_on_attested_repro_scenarios():
    gate = parser["gtk_layout_zero_gate"]
    covered = parser["GTK_LAYOUT_ZERO_GATE_2027"]
    assert covered == frozenset({
        "mainwindow-baseline-1366", "mainwindow-baseline-1600",
        "maintenance-tools-1280", "maintenance-tools-1366", "maintenance-tools-1600",
    })
    clean = {"GtkStack": 0, "GtkRevealer": 0, "stream_truncated": False}
    critical = {"GtkStack": 2, "GtkRevealer": 3, "stream_truncated": False}
    truncated = {"GtkStack": 0, "GtkRevealer": 0, "stream_truncated": True}
    for scenario in covered:
        assert gate(scenario, clean) is None
        assert gate(scenario, critical) == "GTK layout P0 #2027 : GtkStack=2, GtkRevealer=3"
        assert "tronqué" in gate(scenario, truncated)
    assert gate("startup", critical) is None
    assert gate("core-navigation", critical) is None
    assert gate("PRIVATE_TOKEN", critical) is None


def test_runner_restores_zero_geometry_gate_after_live_product_fix():
    source = (MODULE.parent / "run.py").read_text()
    assert "gtk_layout_zero_gate(name, gtk_critical)" in source
    assert 'if geometry_regression is not None:' in source
    assert "assertion_error = geometry_regression" in source
    assert "returncode = 1" in source
    assert 'layout_problem = gtk_layout_critical_failure(gtk_critical)' in source
    assert "_write_redacted_log(stderr_path, errors, env)" in source

