"""Pilot plugins must remain opt-in; explicit user overrides are unaffected."""
from pathlib import Path
import json
import pytest

ROOT = Path(__file__).resolve().parents[1] / "src/plugins"

@pytest.mark.parametrize("name", ("baen", "literary-criticism", "dev-updater", "constellations", "browser-recorder"))
def test_pilot_plugins_are_disabled_by_default(name):
    data = json.loads((ROOT / name / "cifereca-plugin.json").read_text(encoding="utf-8"))
    assert data["plugins"]
    assert all(p.get("enabled_by_default") is False for p in data["plugins"])
