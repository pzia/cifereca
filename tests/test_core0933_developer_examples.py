from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.plugins.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[1] / "docs/plugin-examples"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_public_provider_example_matches_declared_capabilities():
    root = ROOT / "provider"
    manifest = load_manifest(root / "cifereca-plugin.json")
    assert manifest.plugins[0].id == "provider.example"

    module = _module("contract_provider_example", root / "exemple_provider.py")
    provider = module.PLUGIN.activate(NS())
    session = provider.create_session(
        credential_id="public",
        label="Exemple",
        credentials={},
        options={},
        config=None,
        storage=None,
        diagnostics=None,
    )
    session.connect()
    rows = session.search("pédagogique")

    assert len(rows) == 1
    assert (rows[0].source_provider, rows[0].source_credential) == ("example", "public")
    assert session.capabilities.search is True
    assert session.capabilities.loans is False
    assert session.search("absent") == []

    session.close()
    assert session.connected is False
    module.PLUGIN.deactivate()


def test_public_page_example_registers_without_importing_gtk():
    root = ROOT / "page"
    assert load_manifest(root / "cifereca-plugin.json").plugins[0].type == "ui.page"

    module = _module("contract_page_example", root / "exemple_page.py")
    pages = module.PLUGIN.activate(NS()).pages()

    assert pages[0].route == "example-page"
    host = NS(toast=Mock())
    pages[0].actions[0].callback(host)
    host.toast.assert_called_once_with("Exemple")
    module.PLUGIN.deactivate()
