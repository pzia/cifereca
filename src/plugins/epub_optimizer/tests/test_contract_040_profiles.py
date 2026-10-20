import json
from pathlib import Path

from types import SimpleNamespace

from cifereca_epub_optimizer.profiles import discover_profiles, profile_from_service


class DescribeOnlyProfile:
    def describe(self):
        return {
            "id": "third-party", "label": "Third party", "width": 600, "height": 800,
            "grayscale": True, "gray_levels": [0, 255], "jpeg_quality": 75,
            "max_zoom_factor": 2.0, "allow_landscape": True,
            "suffix": "third", "description": "External contract profile",
        }


def test_profile_contract_prefers_describe_without_shared_python_class():
    profile = profile_from_service(DescribeOnlyProfile(), plugin_id="device.profile.third-party", plugin_version="1.0.0")
    assert profile.id == "third-party"
    assert profile.width == 600 and profile.height == 800
    assert profile.max_zoom_factor == 2.0 and profile.allow_landscape is True
    assert profile.plugin_id == "device.profile.third-party"


def test_discovery_filters_by_core041_contract_and_capability():
    calls = []
    row = SimpleNamespace(
        service=DescribeOnlyProfile(), plugin_id="device.profile.third-party", plugin_version="1.0.0"
    )
    class Context:
        logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
        def services(self, category, **kwargs):
            calls.append((category, kwargs))
            return (row,)
    found = discover_profiles(Context())
    assert "device.profile.third-party" in found
    assert calls == [("device.profile", {
        "contract": "cifereca.device.profile",
        "version": ">=1,<2",
        "capabilities": ("device.profile.describe",),
    })]


def test_manifest_requires_plugin_api_14_for_artifact_contracts():
    manifest = json.loads((Path(__file__).resolve().parents[1] / "cifereca-plugin.json").read_text(encoding="utf-8"))
    assert manifest["package"]["plugin_api"] == ">=1.4,<2"
    assert manifest["package"]["version"] == "0.3.5"
    assert {row["version"] for row in manifest["plugins"]} == {"0.3.5"}
    ui = next(row for row in manifest["plugins"] if row["id"] == "ui.page.epub-optimizer")
    assert {dep["version"] for dep in ui["requires"]} == {">=0.3.1,<1"}
