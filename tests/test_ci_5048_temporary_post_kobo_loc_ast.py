"""Temporary exact post-Kobo provider coupling census (#5048); never merge."""
from pathlib import Path
import json
import warnings

from scripts.ci_provider_core_coupling_inventory import inventory
from scripts.ci_provider_core_coupling_report import extended_inventory


def test_p0_main_provider_loc_ast_post_kobo():
    root = Path(__file__).resolve().parents[1]
    report = extended_inventory(root)
    physical = inventory(root)
    fields = {
        "baseline_commit": "150c252775fe84cd5322875b2493c262313ea407",
        "named_provider_modules": physical["dedicated_per_file"],
        "extra_named_modules": report["vendor_named_modules_outside_legacy_dedicated_scope"],
        "physical_total": report["total_core_python_physical_lines"],
        "physical_partition": report["line_partition"],
        "ast_by_alias": report["generic_ast_signal_occurrences_by_provider_alias"],
        "ast_by_kind": report["generic_ast_signal_occurrences_by_kind"],
        "parse_errors": report["historical"]["parse_errors"],
    }
    warnings.warn("P0_5048_POST_KOBO_MAIN=" + json.dumps(fields, sort_keys=True, ensure_ascii=True))
    assert not fields["parse_errors"]
