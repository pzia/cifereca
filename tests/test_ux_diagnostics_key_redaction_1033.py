"""#3135: formatting preserves technical keys except explicit login/password.

The old #1033 policy masked emails, URLs and paths. The product contract of
2026-10-25 instead retains them as far as the UI's finite preview allows.
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
    sys.path.insert(0, str(PATH.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module.format_diagnostic_detail


@pytest.mark.parametrize("mode", ("readable", "json", "raw"))
@pytest.mark.parametrize("value", (
    "alice@example.com",
    "https://example.org/path?token=confidential",
    "/home/alice/private/book.epub",
))
def test_noncredential_mapping_keys_remain_visible(mode, value):
    data = {value: "safe"}
    shown = formatter()(data, mode=mode)
    assert value in shown
    if mode != "readable":
        assert json.loads(shown) == data


@pytest.mark.parametrize("mode", ("readable", "json", "raw"))
@pytest.mark.parametrize("technical_key", (
    "alice@example.com",
    "https://example.org/path?token=confidential",
    "/home/alice/private/book.epub",
))
def test_mapping_key_at_preview_boundary_is_bounded_not_pseudonymized(mode, technical_key):
    key = "x" * 151 + technical_key
    shown = formatter()({key: "safe"}, mode=mode)
    # The 160-character widget limit is a presentation cap, not censorship.
    assert "x" * 80 in shown
    assert "safe" in shown
    assert len(shown) < 300


def test_json_key_remains_unmasked_until_visible_preview_limit():
    key = "x" * 151 + "camille@example.com"
    obj = json.loads(formatter()({key: 0}, mode="json"))
    (visible_key,) = tuple(obj)
    assert visible_key.startswith("x" * 151)
    assert "camille@" in visible_key
    assert obj[visible_key] == 0


@pytest.mark.parametrize("mode", ("readable", "json", "raw"))
def test_oversized_mapping_key_has_bounded_visible_prefix(mode):
    key = "x" * 24_001 + "alice@example.com"
    shown = formatter()({key: "safe"}, mode=mode)
    assert "x" * 80 in shown
    assert "safe" in shown
    assert len(shown) < 300
    assert "alice@" not in shown  # naturally outside the preview, not redacted
