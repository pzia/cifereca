from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "src/cifereca/services.py"


def _method_source(name: str) -> str:
    tree = ast.parse(SERVICES.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    body = method.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


def test_single_source_placement_uses_only_attested_complete_acquisition():
    source = _method_source("local_placement_for_source")

    assert "self.storage.acquisitions()" in source
    assert "row.get('status') == 'complete'" in source
    assert "row.get('identity_id') == identity_id" in source
    assert "row.get('provider_id') == provider_id" in source
    assert "row.get('credential_id') == credential_id" in source
    assert "str(row.get('item_id') or '') == str(item_id)" in source


def test_source_placement_never_matches_by_title():
    source = _method_source("local_placement_for_source")
    assert "title" not in source.lower()


def test_source_placement_requires_exactly_one_present_local_copy():
    source = _method_source("local_placement_for_source")

    assert "row.get('state') == 'present'" in source
    assert "row.get('artifact_id') in artifact_ids" in source
    assert "if len(rows) != 1:" in source
    assert "return None" in source


def test_missing_file_removes_local_match():
    source = _method_source("local_placement_for_source")

    assert "self.local_book_path(rows[0]['placement_id'], identity_id=identity_id)" in source
    assert "except ProviderConfigurationError:" in source
    assert "return None" in source


def test_bulk_source_placement_keeps_ambiguous_artifacts_unlinked():
    source = _method_source("local_placements_for_sources")

    assert "self.storage.acquisitions(status='complete')" in source
    assert "placements.setdefault(row.get('artifact_id'), []).append(row)" in source
    assert "if len(rows) != 1:" in source
    assert "continue" in source


def test_source_placement_read_path_has_no_sync_or_acquisition_side_effect():
    single = _method_source("local_placement_for_source")
    bulk = _method_source("local_placements_for_sources")

    forbidden = (
        "sync",
        "opening_sync",
        "search(",
        "download",
        "execute_acquisition",
        "catalog_scan",
    )
    for token in forbidden:
        assert token not in single
        assert token not in bulk
