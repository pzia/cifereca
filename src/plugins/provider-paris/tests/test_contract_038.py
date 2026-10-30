from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Contract040Tests(unittest.TestCase):
    def test_manifest_explicit_negotiation_fields(self):
        data = json.loads((ROOT / "cifereca-plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(data.get("manifest"), 1)
        self.assertEqual(data.get("package", {}).get("plugin_api"), ">=1.4.3,<2")
        self.assertTrue(data.get("plugins"))
        package_version = data["package"]["version"]
        for plugin in data["plugins"]:
            self.assertEqual(plugin.get("version"), package_version)
            for field in ("requires", "recommends", "capabilities", "requires_brokers", "requires_contracts", "system_requires"):
                self.assertIn(field, plugin, f"{plugin.get('id')}: missing {field}")
                self.assertIsInstance(plugin[field], list)
            self.assertTrue(str(plugin.get("help_text") or "").strip())
            self.assertNotIn("requires_services", plugin)

    def test_no_private_cifereca_import(self):
        violations = []
        for path in ROOT.rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("cifereca."):
                    if node.module != "cifereca.plugin_api":
                        violations.append(f"{path.name}: from {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("cifereca.") and alias.name != "cifereca.plugin_api":
                            violations.append(f"{path.name}: import {alias.name}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
