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
