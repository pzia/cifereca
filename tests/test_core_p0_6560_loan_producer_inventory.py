"""#3197: every shipped loan producer publishes the typed inventory contract.

Manifest inventory is exhaustive for the in-tree packages, not installed
third-party extensions. No session construction or network access.
"""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest

from cifereca.providers.base import ProviderSession


ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins"
PRODUCERS = {
    "provider.paris": (
        "provider-paris", "cifereca_paris", "ParisProviderSession", "paris",
    ),
    "provider.bnf-pret": (
        "provider-bnf-pret", "cifereca_bnf_pret", "BnfProviderSession", "bnf-pret",
    ),
    "provider.biblioaccess.la-rochelle": (
        "provider-biblioaccess", "cifereca_biblioaccess",
        "LaRochelleSession", "biblioaccess.la-rochelle",
    ),
}


def test_all_shipped_loan_manifests_have_a_typed_producer_witness():
    found = {}
    for manifest in sorted(ROOT.glob("*/cifereca-plugin.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for plugin in data["plugins"]:
            if "sync.loans" not in plugin.get("capabilities", ()):
                continue
            assert plugin["type"] == "provider.library"
            assert plugin["id"] not in found
            found[plugin["id"]] = manifest.parent.name
    assert found == {key: value[0] for key, value in PRODUCERS.items()}


@pytest.mark.parametrize("plugin_id", sorted(PRODUCERS))
def test_real_loan_producer_has_independent_typed_reader(plugin_id, monkeypatch):
    directory, module_name, class_name, provider_id = PRODUCERS[plugin_id]
    monkeypatch.syspath_prepend(str(ROOT / directory))
    module = importlib.import_module(module_name)
    assert Path(module.__file__).resolve().parent == (ROOT / directory).resolve()
    descriptor = module.DESCRIPTOR
    session_type = getattr(module, class_name)
    assert descriptor.id == provider_id
    assert descriptor.capabilities.loans is True
    assert issubclass(session_type, ProviderSession)
    reader = inspect.getattr_static(session_type, "loan_inventory_snapshot")
    assert inspect.isfunction(reader)
    assert reader is not inspect.getattr_static(ProviderSession, "loans")
    assert tuple(inspect.signature(reader).parameters) == ("self", "account")
