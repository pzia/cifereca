"""#3135: display bounding must never be confused with broad redaction.

Only explicit login/password values are pseudonymized; paths, headers, URLs,
emails, tokens and other source information are preserved when visible.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"


def _module():
    spec = importlib.util.spec_from_file_location("diagnostics_redaction_boundary", PLUGIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PLUGIN.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
@pytest.mark.parametrize("start", [789, 790, 791, 792])
def test_email_at_preview_boundary_remains_visible_up_to_limit(mode, start):
    value = "x" * start + "alice@example.com"
    rendered = _module().format_diagnostic_detail({"message": value}, mode=mode)
    # The GUI shows the original prefix, then an explicit finite-preview notice.
    assert "alice@" in rendered
    assert "[tronqué]" in rendered
    assert len(rendered) < 950


def test_json_preview_keeps_non_login_email_field_value():
    obj = {"message": "z" * 791 + "camille@example.com"}
    shown = json.loads(_module().format_diagnostic_detail(obj, mode="json"))
    assert "camille@" in shown["message"]
    assert "[tronqué]" in shown["message"]


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
@pytest.mark.parametrize("value", [
    "Bearer VERY_SECRET_CREDENTIAL",
    "token=VERY_SECRET_CREDENTIAL",
    "https://licenses.example/fulfill?token=VERY_SECRET_CREDENTIAL",
    "/home/alexandre/private/VERY_SECRET_CREDENTIAL",
])
def test_other_technical_values_are_not_expurgated(mode, value):
    result = _module().format_diagnostic_detail({"message": value}, mode=mode)
    assert value in result


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_oversized_text_is_bounded_not_wholesale_suppressed(mode):
    result = _module().format_diagnostic_detail(
        {"message": "x" * 24_001 + "alice@example.com"}, mode=mode
    )
    assert "x" * 80 in result
    assert "alice" not in result  # beyond 800-character preview window
    assert "[tronqué]" in result
    assert len(result) < 1000


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_login_password_are_pseudonymized_but_other_secrets_are_not(mode):
    raw = {"login": "reader@example.invalid", "password": "ABC123",
           "token": "ABC123", "url": "https://books.test/path?token=ABC123"}
    shown = _module().format_diagnostic_detail(raw, mode=mode)
    assert "reader@example.invalid" not in shown
    # Identical data in a non-password field remains visible.
    assert "ABC123" in shown
    assert "https://books.test/path?token=ABC123" in shown
    assert "login-" in shown and "mot-de-passe-" in shown
