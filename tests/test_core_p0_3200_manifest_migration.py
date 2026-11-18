"""Canonical publication with intentionally compatible v1 installation."""
from copy import deepcopy
import json
from pathlib import Path
import zipfile

import pytest

from cifereca.plugins.manifest import load_manifest, parse_manifest
from cifereca.plugins.errors import ManifestError


def _manifest():
    return {
        "manifest": 1,
        "package": {"id": "sample.package", "name": "Sample", "version": "1.0",
                    "plugin_api": ">=1,<2"},
        "plugins": [{"id": "sample.plugin", "type": "ui.page",
                     "entrypoint": "sample:Plugin",
                     "requires_brokers": ["catalog"],
                     "capabilities": ["ui.page"],
                     "system_requires": [{"type": "python-package", "name": "Pillow"}]}],
    }


def test_legacy_requires_services_and_distribution_alias_are_advisory(tmp_path, caplog):
    canonical = _manifest()
    legacy = deepcopy(canonical)
    row = legacy["plugins"][0]
    row["requires_services"] = row.pop("requires_brokers")
    row["system_requires"][0]["type"] = "python"
    row["capabilities"] = ["opaque-private-value"]
    row["requires_contracts"] = [{"category": "ui.page", "contract": "demo",
                                  "capabilities": ["noncanonical"]}]
    path = tmp_path / "cifereca-plugin.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    parsed = load_manifest(path)
    d = parsed.plugins[0]
    assert d.requires_services == ("catalog",)
    assert d.requires_brokers == ()
    assert d.system_requires == parse_manifest(canonical).plugins[0].system_requires
    assert d.capabilities == ("opaque-private-value",)
    assert d.requires_contracts[0].capabilities == ("noncanonical",)
    assert len(parsed.migration_warnings) == 4
    assert "use requires_brokers" in caplog.text
    assert "use python-package" in caplog.text
    assert "opaque-private-value" not in caplog.text
    assert str(tmp_path) not in caplog.text
    assert not parse_manifest(canonical).migration_warnings


def test_python_interpreter_constraint_is_not_a_distribution_alias():
    data = _manifest()
    data["plugins"][0]["system_requires"] = [{"type": "python", "version": ">=3.11"}]
    manifest = parse_manifest(data)
    assert manifest.plugins[0].system_requires[0].type == "python"
    assert not manifest.migration_warnings


@pytest.mark.parametrize("mutation", ["services", "alias", "missing_name", "incompatible"])
def test_publication_fence_and_legacy_parser_are_distinct(mutation):
    from scripts.plugin_packaging import validate_official_manifest_syntax, PackagingError
    data = _manifest()
    row = data["plugins"][0]
    if mutation == "services":
        row["requires_services"] = ["catalog"]
    elif mutation == "alias":
        row["system_requires"][0]["type"] = "python"
    elif mutation == "missing_name":
        row["system_requires"][0].pop("name")
    else:
        data["package"]["plugin_api"] = ">=999"
    if mutation in ("services", "alias"):
        assert parse_manifest(data).migration_warnings
        with pytest.raises(PackagingError):
            validate_official_manifest_syntax(data)
    else:
        with pytest.raises(ManifestError):
            parse_manifest(data)


def test_all_official_source_manifests_use_canonical_syntax():
    from scripts.plugin_packaging import discover_plugins, read_plugin_manifest, validate_official_manifest_syntax
    root = Path(__file__).resolve().parents[1] / "src" / "plugins"
    packages = discover_plugins(root)
    assert packages, "no official packages discovered"
    for package in packages:
        raw = read_plugin_manifest(package)
        validate_official_manifest_syntax(raw)
        assert not parse_manifest(raw).migration_warnings, package.name


def test_official_bundle_fence_does_not_reject_legacy_external_bundle(tmp_path, monkeypatch):
    from scripts import plugin_packaging as packaging
    root = tmp_path / "official"
    official = root / "sample"
    external = tmp_path / "external" / "sample"
    monkeypatch.setattr(packaging, "PLUGINS_DIR", root)
    data = _manifest()
    data["plugins"][0]["requires_services"] = ["catalog"]
    for package in (official, external):
        package.mkdir(parents=True)
        (package / "sample.py").write_text("class Plugin: pass\n", encoding="utf-8")
        (package / "cifereca-plugin.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(packaging.PackagingError, match="requires_services"):
        packaging.build_plugin_zip(official, tmp_path / "out")
    archive = packaging.build_plugin_zip(external, tmp_path / "out")
    with zipfile.ZipFile(archive) as zip_file:
        raw = json.loads(zip_file.read("cifereca-plugin.json"))
    assert parse_manifest(raw).plugins[0].requires_services == ("catalog",)
