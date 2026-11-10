"""Regression tests for the shared plugin contract gate."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/validate_plugin_contract.py"
spec = importlib.util.spec_from_file_location("plugin_contract_validator", SCRIPT)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def _package(tmp_path):
    root = tmp_path / "provider-kobo"
    root.mkdir()
    data = {
        "manifest": 1,
        "package": {"version": "1.0", "plugin_api": ">=1.4.3,<2"},
        "plugins": [{
            "id": "provider.kobo", "version": "1.0", "help_text": "Help",
            **{field: [] for field in validator.FIELDS},
        }],
    }
    (root / "cifereca-plugin.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "impl.py").write_text("from cifereca.plugin_api import Account\n", encoding="utf-8")
    return root, data


def test_valid_plugin_and_public_api_import(tmp_path):
    root, _ = _package(tmp_path)
    assert validator.validate(root) == []


def test_rejects_manifest_drift_and_private_import(tmp_path):
    root, data = _package(tmp_path)
    data["plugins"][0].pop("requires_contracts")
    data["plugins"][0]["requires_services"] = []
    data["package"]["plugin_api"] = ">=1,<2"
    (root / "cifereca-plugin.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "impl.py").write_text("from cifereca.storage import Storage\n", encoding="utf-8")
    issues = validator.validate(root)
    assert any("requires_contracts" in issue for issue in issues)
    assert any("requires_services" in issue for issue in issues)
    assert any("plugin_api" in issue for issue in issues)
    assert any("private import" in issue for issue in issues)


def test_invalid_source_fails_closed_but_tests_are_excluded(tmp_path):
    root, _ = _package(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_other.py").write_text("from cifereca.storage import Storage\n", encoding="utf-8")
    assert validator.validate(root) == []
    (root / "impl.py").write_text("def invalid(:\n", encoding="utf-8")
    assert any("unparseable source" in issue for issue in validator.validate(root))
