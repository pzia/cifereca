"""P0 #5975 / #5851: public Plugin API must not import GTK diagnostics."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_plugin_api_import_remains_headless_in_fresh_process():
    code = r"""
import importlib.abc
import sys

class NoGUI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'cifereca.gui' or fullname.startswith('cifereca.gui.'):
            raise AssertionError('Plugin API attempted GUI import: ' + fullname)
        return None

sys.meta_path.insert(0, NoGUI())
import cifereca.plugin_api as api
from cifereca.diagnostics_pseudonyms import DiagnosticsPseudonyms, DISPLAY_PSEUDONYMS
assert api.DiagnosticsPseudonyms is DiagnosticsPseudonyms
assert api.DISPLAY_PSEUDONYMS is DISPLAY_PSEUDONYMS
assert not any(x == 'cifereca.gui' or x.startswith('cifereca.gui.')
               for x in sys.modules), [x for x in sys.modules if x.startswith('cifereca.gui')]
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        timeout=45, env=os.environ.copy(),
    )
    assert result.returncode == 0, (
        f"headless import failed with {result.returncode}: "
        + result.stderr[-3000:]
    )


def test_public_legacy_and_neutral_paths_share_exact_singleton_and_class():
    from cifereca.diagnostics_pseudonyms import (
        DiagnosticsPseudonyms as neutral_class,
        DISPLAY_PSEUDONYMS as neutral_global,
    )
    from cifereca.plugin_api import (
        DiagnosticsPseudonyms as api_class,
        DISPLAY_PSEUDONYMS as api_global,
    )
    from cifereca.gui.diagnostics_pseudonyms import (
        DiagnosticsPseudonyms as gui_class,
        DISPLAY_PSEUDONYMS as gui_global,
    )
    assert api_class is neutral_class is gui_class
    assert api_global is neutral_global is gui_global
    renderer = neutral_class()
    observed = renderer.text(
        'login=alice@example.test password="secret" '
        'cookie=unmasked token=unmasked title="Readable Book"'
    )
    assert 'login=login-1' in observed
    assert 'password="mot-de-passe-1"' in observed
    assert "cookie=unmasked token=unmasked" in observed
    assert 'title="Readable Book"' in observed
    assert renderer.text(observed) == observed
    assert renderer.text("login=alice@example.test") == "login=login-1"


def test_neutral_pseudonym_module_has_no_plugin_or_gui_import():
    root = Path(__file__).resolve().parents[1]
    module = (root / "src/cifereca/diagnostics_pseudonyms.py").read_text(
        encoding="utf-8"
    )
    assert "from cifereca.gui" not in module
    assert "import gi" not in module
    assert "from cifereca.plugin_api" not in module


@pytest.mark.parametrize("raw", ["password=abc", "username='alice'", "login=bob"])
def test_legacy_rendered_fields_are_preserved(raw):
    from cifereca.diagnostics_pseudonyms import DiagnosticsPseudonyms
    renderer = DiagnosticsPseudonyms()
    transformed = renderer.text(raw)
    assert transformed != raw
    assert renderer.text(transformed) == transformed
