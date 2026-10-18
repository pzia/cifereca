"""The GTK probe may acknowledge only its proven synthetic provider failure."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.providers import ProviderConfigurationError


@pytest.mark.parametrize("error,body,tagged", [
    (ProviderConfigurationError("Provider non connecté: local"), "Traitement indisponible", True),
    (ProviderConfigurationError("Provider non connecté: remote"), "Traitement indisponible", False),
    (RuntimeError("Provider non connecté: local"), "Traitement indisponible", False),
    (ProviderConfigurationError("Provider non connecté: local"), "PRIVATE-TOKEN", False),
    (RuntimeError("PRIVATE-TOKEN"), "Traitement indisponible", False),
])
def test_only_exact_fixture_error_can_attest_its_own_new_redacted_dialog(error, body, tagged):
    path = Path(__file__).parents[1] / "e2e/diagnostics_navigation_probe.py"
    tree = ast.parse(path.read_text())
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "present_offline_fixture_error")
    ns = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), ns)
    class Dialog:
        def get_body(self): return body
    prior, created = Dialog(), Dialog()
    dialogs, calls = [prior], []
    def present(payload):
        calls.append(payload)
        dialogs.append(created)
    ns["present_offline_fixture_error"](present, error, dialogs=lambda: dialogs)
    assert calls == [error]
    assert getattr(created, "_cifereca_attested_offline_fixture", False) is tagged
    assert not hasattr(prior, "_cifereca_attested_offline_fixture")
