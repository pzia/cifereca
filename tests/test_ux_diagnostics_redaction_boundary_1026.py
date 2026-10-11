"""UX security regression for #1026: redaction before display truncation.

No account, network or GTK required. Run against the plugin's pure formatter.
These are now positive regression tests: an XPASS marker would hide a leak.
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


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
@pytest.mark.parametrize("start", [789, 790, 791, 792])
def test_partial_email_at_truncation_boundary_never_reaches_output(mode, start):
    formatter = _module().format_diagnostic_detail
    # Cut before '.com', so matching the email regex on the sliced text fails.
    payload = "x" * start + "alice@example.com"
    displayed = formatter({"message": payload}, mode=mode)
    assert "alice@" not in displayed
    assert "example" not in displayed


def test_json_exportable_detail_redacts_email_before_cutting():
    import json

    formatter = _module().format_diagnostic_detail
    displayed = formatter({"message": "z" * 791 + "camille@example.com"}, mode="json")
    assert json.loads(displayed)["message"].find("camille@") == -1

@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
@pytest.mark.parametrize("sensitive", [
    "Bearer VERY_SECRET_CREDENTIAL",
    "token=VERY_SECRET_CREDENTIAL",
    "https://licenses.example/fulfill?token=VERY_SECRET_CREDENTIAL",
    "/home/alexandre/private/VERY_SECRET_CREDENTIAL",
])
def test_other_secrets_crossing_preview_boundary_are_redacted(mode, sensitive):
    fmt = _module().format_diagnostic_detail
    rendered = fmt({"message": " " * 795 + sensitive}, mode=mode)
    assert "VERY_SECRET_CREDENTIAL" not in rendered
    assert "/home/alexandre/" not in rendered
    assert "https://licenses.example" not in rendered


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_oversized_text_is_suppressed_before_any_regex_or_clipboard(mode):
    rendered = _module().format_diagnostic_detail(
        {"message": "x" * 24_001 + "alice@example.com"}, mode=mode
    )
    assert "alice" not in rendered
    assert "x" * 80 not in rendered
    assert "contenu masqué" in rendered
