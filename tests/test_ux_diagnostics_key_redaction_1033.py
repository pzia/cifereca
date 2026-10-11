"""UX P0 #1012: guard against partial secrets in diagnostic mapping keys.

Strict xfail records the outstanding vulnerability without claiming a fix.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

PATH = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"


def formatter():
    spec = importlib.util.spec_from_file_location("ux_diagnostics_key_boundary", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # The diagnostics page imports its peer diagnostics_explorer module.
    # Resolve it when testing the page from the source tree.
    sys.path.insert(0, str(PATH.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module.format_diagnostic_detail


@pytest.mark.xfail(strict=True, reason="#1012: mapping keys are cut at 160 before redaction")
@pytest.mark.parametrize("mode", ("readable", "json", "raw"))
@pytest.mark.parametrize("sensitive", (
    "alice@example.com",
    "https://example.org/path?token=confidential",
    "/home/alice/private/book.epub",
))
def test_key_crossing_160_character_boundary_never_discloses_fragment(mode, sensitive):
    key = "x" * 151 + sensitive
    shown = formatter()({key: "safe"}, mode=mode)
    assert "alice@" not in shown
    assert "https://" not in shown
    assert "/home/" not in shown


@pytest.mark.xfail(strict=True, reason="#1012: JSON key must be redacted before truncation")
def test_json_key_is_expurgated_before_preview_limit():
    key = "x" * 151 + "camille@example.com"
    obj = json.loads(formatter()({key: 0}, mode="json"))
    assert not any("camille@" in candidate for candidate in obj)
