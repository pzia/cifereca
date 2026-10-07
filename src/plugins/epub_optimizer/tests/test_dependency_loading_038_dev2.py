from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DependencyLoadingDev2Tests(unittest.TestCase):
    def test_pillow_is_a_python_package_requirement_only_for_consumers(self):
        data = json.loads((ROOT / "cifereca-plugin.json").read_text(encoding="utf-8"))
        by_id = {row["id"]: row for row in data["plugins"]}
        pillow = {"type": "python-package", "name": "Pillow", "version": ">=11,<13", "import": "PIL"}
        pymupdf = {"type": "python-package", "name": "PyMuPDF", "version": ">=1.26,<2", "import": "pymupdf"}
        self.assertEqual(by_id["document.transformer.epub-optimizer"]["system_requires"], [pillow])
        self.assertEqual(by_id["document.transformer.pdf-eink-optimizer"]["system_requires"], [pillow, pymupdf])
        self.assertEqual(by_id["ui.page.epub-optimizer"]["system_requires"], [pillow, pymupdf])
        for plugin_id in (
            "device.profile.xteink-x3", "device.profile.xteink-x4",
            "device.profile.kobo-clara-bw", "device.profile.generic-eink",
        ):
            self.assertEqual(by_id[plugin_id]["system_requires"], [])

    def test_profile_import_chain_does_not_reference_engine_or_pil(self):
        paths = [
            ROOT / "cifereca_epub_optimizer" / "__init__.py",
            ROOT / "cifereca_epub_optimizer" / "profiles.py",
            ROOT / "cifereca_epub_optimizer" / "builtin_profiles.py",
            ROOT / "cifereca_epub_optimizer_profiles.py",
        ]
        violations = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.endswith("engine") or module == "PIL" or module.startswith("PIL."):
                        violations.append(f"{path.name}: {module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "PIL" or alias.name.startswith("PIL."):
                            violations.append(f"{path.name}: {alias.name}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
