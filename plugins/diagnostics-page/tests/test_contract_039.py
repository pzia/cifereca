import json
from pathlib import Path


def test_manifest_declares_diagnostics_and_tasks_brokers():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "cifereca-plugin.json").read_text())
    plugin = data["plugins"][0]
    assert plugin["type"] == "ui.page"
    assert plugin["requires_brokers"] == ["diagnostics", "tasks"]
    assert "requires_services" not in plugin


def test_runtime_import_is_public_api_only():
    source = (Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py").read_text()
    assert "cifereca.diagnostics" not in source
    assert "cifereca.gui" not in source
    assert 'services("runtime.adapter"' not in source
    assert ".probe()" not in source
