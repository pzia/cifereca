"""P0 #4858: no second Kobo DRM interpretation in the generic Core facade."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_unused_kobo_drm_normalization_is_absent_from_library_service():
    tree = ast.parse((ROOT / "src/cifereca/services.py").read_text())
    service = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef)
                   and node.name == "LibraryService")
    methods = {node.name for node in service.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_kobo_canonical_drm" not in methods
    # The legacy executor's existing adapter remains its only DRM normalizer.
    adapter = (ROOT / "src/cifereca/kobo/acquisition.py").read_text()
    assert "def canonical_drm(value: Any)" in adapter
