"""Ephemeral #5048 exact main provider coupling measurement; close PR after reading CI."""
import json
import warnings

from scripts.ci_provider_core_coupling_report import extended_inventory


def test_main_provider_core_coupling_telemetry():
    report = extended_inventory(__import__("pathlib").Path(__file__).resolve().parents[1])
    snapshot = {
        "main_base": "7b8a087e22cec12d3c3383b96cfcc098f1203466",
        "historical": report["historical"],
        "physical_lines": report["total_core_python_physical_lines"],
        "partition": report["line_partition"],
        "expanded_named": report["expanded_named_modules"],
        "signal_occurrences_by_alias": report["generic_ast_signal_occurrences_by_provider_alias"],
        "signal_occurrences_by_kind": report["generic_ast_signal_occurrences_by_kind"],
        "top_signal_files": report["top_generic_by_ast_signal"][:8],
    }
    warnings.warn("P0_5048_MAIN_LOC_AST_SNAPSHOT=" + json.dumps(snapshot, ensure_ascii=True, sort_keys=True), UserWarning)
    assert not report["historical"]["parse_errors"]
