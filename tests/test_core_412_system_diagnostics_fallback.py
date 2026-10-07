from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from cifereca.builtin_ui.system_page import SystemPages
from cifereca.gui.shell_model import resolve_pages
from cifereca.plugin_api import UiPageContribution


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src/cifereca/builtin_ui/cifereca-plugin.json"


class Diagnostics:
    def system_status(self):
        return {"version": "test", "installed_at": None, "plugins": [], "ready": 0, "attention": 0}


class Context:
    def __init__(self):
        self.diagnostics = Diagnostics()

    def broker(self, broker_id):
        return self.diagnostics if broker_id == "diagnostics" else None


def test_system_pages_uses_public_diagnostics_broker_and_unified_route():
    plugin = SystemPages().activate(Context())
    page = plugin.pages()[0]

    assert page.route == "diagnostics"
    assert page.group == "configuration"
    assert "secours" in page.title.casefold()
    assert plugin.diagnostics is not None


def test_builtin_manifest_marks_system_page_as_low_priority_diagnostics_fallback():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    row = next(item for item in data["plugins"] if item["id"] == "core.ui.system")

    assert row["priority"] < 0
    assert row["requires_brokers"] == ["diagnostics"]
    assert "repli" in row["display_name"].casefold()
    assert "diagnostics" in row["help_text"].casefold()


def test_external_diagnostics_page_replaces_builtin_fallback_on_same_route():
    fallback = replace(SystemPages().pages()[0], priority=-100)
    external = UiPageContribution(
        route="diagnostics",
        title="Diagnostics",
        priority=80,
        factory=lambda host: object(),
        group="configuration",
    )

    resolved = resolve_pages([fallback, external])

    assert len([page for page in resolved if page.route == "diagnostics"]) == 1
    assert next(page for page in resolved if page.route == "diagnostics").title == "Diagnostics"


def test_system_page_source_no_longer_depends_on_private_host_system_status():
    source = (ROOT / "src/cifereca/builtin_ui/system_page.py").read_text(encoding="utf-8")

    assert "host.system_status()" not in source
    assert "broker.system_status()" in source
