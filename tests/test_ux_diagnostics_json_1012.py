"""UX contract #1012: formatted diagnostics, without importing GTK.

These strict xfails document known deficits of the presentation formatter.
They must only become green after a real Plugins implementation and an
independent GTK review. No network or real account data are used.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"


def _formatter():
    """Import the real pure Python formatter, without instantiating GTK."""
    import sys
    plugin_dir = str(PATH.parent)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    from cifereca_diagnostics_page import _safe_text
    return _safe_text


@pytest.mark.xfail(strict=True, reason="#1012: structured values presently use repr")
def test_1012_nested_object_is_indented_parseable_json():
    fmt = _formatter()
    payload = {"source": "Paris", "details": {"status": False, "attempts": 0, "items": [1, None]}}
    shown = fmt(payload)
    assert "\n" in shown and "  " in shown, "Details should be indented"
    assert json.loads(shown) == payload, "JSON must retain booleans/null/numbers"


@pytest.mark.xfail(strict=True, reason="#1012: Unicode and quotes must survive round trip")
def test_1012_unicode_and_multiline_strings_are_escaped_in_json():
    fmt = _formatter()
    payload = {"message": "Échec « prêt »", "detail": 'ligne 1\nligne "2"'}
    shown = fmt(payload)
    assert json.loads(shown) == payload
    assert "Échec « prêt »" in shown, "Do not lose human-readable Unicode"


@pytest.mark.xfail(strict=True, reason="#1012: list values must not be Python repr")
def test_1012_list_keeps_nested_types_and_is_json():
    fmt = _formatter()
    payload = [{"status": None}, {"status": False}, {"status": 0}, {"status": ""}]
    shown = fmt(payload)
    assert json.loads(shown) == payload
    assert shown.count("\n") >= len(payload), "List items should be readable"


def test_1012_plain_messages_are_not_forced_into_json():
    fmt = _formatter()
    assert fmt("erreur réseau\nréessayez plus tard") == "erreur réseau\nréessayez plus tard"
