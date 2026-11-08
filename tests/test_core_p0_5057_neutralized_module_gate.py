"""P0 #5057: do not reintroduce provider branches in cut-over Core surfaces.

This is a narrowly scoped zero-baseline, not a claim that all of Core has
been migrated. Expand only after a module's real provider-specific consumers
have been replaced by a versioned neutral capability.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci_provider_core_coupling_inventory import inspect_source


ROOT = Path(__file__).resolve().parents[1]
NEUTRALIZED = (
    "src/cifereca/gui/library_rows.py",
    "src/cifereca/core/library_evidence_scope.py",
    "src/cifereca/core/source_refresh_policy.py",
)


@pytest.mark.parametrize("relative_path", NEUTRALIZED)
def test_no_direct_provider_imports_or_dispatch_in_neutralized_core(relative_path):
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    result = inspect_source(source)
    assert result["imports"] == [], (relative_path, result["imports"])
    assert result["comparisons"] == [], (relative_path, result["comparisons"])


def test_gate_detects_hard_coded_branch_and_relative_implementation_import():
    sample = '''
from ..core import kobo_backup
if provider_id in ("numilog", "bnf-pret"):
    pass
'''
    result = inspect_source(sample)
    assert result["imports"][0]["providers"] == ["kobo"]
    assert result["comparisons"][0]["providers"] == ["bnf-pret", "numilog"]
