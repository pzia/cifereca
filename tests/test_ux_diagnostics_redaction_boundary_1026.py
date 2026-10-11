"""UX security regression for #1026: redaction before display truncation.

No account, network or GTK required. Run against the plugin's pure formatter.
The strict xfail is an intentionally *unresolved* acceptance criterion.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PLUGIN = (
    Path(__file__).resolve().parents[1]
    / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"
)


def _module():
    """Import only the locally shipped plugin; no GTK window is opened."""
    spec = importlib.util.spec_from_file_location("diagnostics_redaction_boundary", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.mark.xfail(
    strict=True,
    reason="#1026: redact the entire string BEFORE the 800-char preview cut",
)
@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
@pytest.mark.parametrize("start", [789, 790, 791, 792])
def test_partial_email_at_truncation_boundary_never_reaches_output(mode, start):
    formatter = _module().format_diagnostic_detail
    # Cut before '.com', so matching the email regex on the sliced text fails.
    payload = "x" * start + "alice@example.com"
    displayed = formatter({"message": payload}, mode=mode)
    assert "alice@" not in displayed
    assert "example" not in displayed


@pytest.mark.xfail(
    strict=True,
    reason="#1026: the clipboard must not receive truncated sensitive fragments",
)
def test_json_exportable_detail_redacts_email_before_cutting():
    import json

    formatter = _module().format_diagnostic_detail
    displayed = formatter({"message": "z" * 791 + "camille@example.com"}, mode="json")
    assert json.loads(displayed)["message"].find("camille@") == -1
