from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "src/cifereca/services.py"
CLI = ROOT / "src/cifereca/cli.py"


def _method_source(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    body = method.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


def test_identity_statistics_is_persisted_projection_only():
    source = _method_source(SERVICES, "LibraryService", "identity_statistics")

    assert "self.storage.placements(state='present')" in source
    assert "self.storage.artifacts()" in source
    assert "self.storage.entitlements(state='active')" in source
    assert "self.storage.metadata_get('loan_statistics:' + str(identity_id))" in source

    forbidden = (
        "refresh_loans(",
        "catalog_scan(",
        "_session_for",
        "execute_acquisition",
        "download",
        "opening_sync",
    )
    for token in forbidden:
        assert token not in source


def test_statistics_distinguish_books_placements_rights_and_unknown_loans():
    source = _method_source(SERVICES, "LibraryService", "identity_statistics")

    assert "'local_books': len(ids)" in source
    assert "'local_placements': len(placements)" in source
    assert "'known_loans': len(loan_keys) if selected else None" in source
    assert "'remote_owned_rights': len(owned)" in source


def test_statistics_keep_format_source_and_cover_breakdowns():
    source = _method_source(SERVICES, "LibraryService", "identity_statistics")

    assert "'formats': dict(formats)" in source
    assert "'sources': dict(sources)" in source
    assert "'epub_covers': dict(covers)" in source
    assert "'extracted' if meta.get('epub_cover')" in source


def test_dashboard_reuses_identity_statistics_projection():
    source = _method_source(SERVICES, "LibraryService", "dashboard_state")

    assert "'statistics': self.identity_statistics(identity.identity_id)" in source


def test_cli_statistics_uses_identity_statistics_projection():
    tree = ast.parse(CLI.read_text())

    has_statistics_branch = any(
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Attribute)
        and isinstance(node.left.value, ast.Name)
        and node.left.value.id == "a"
        and node.left.attr == "cmd"
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value == "statistics"
        for node in ast.walk(tree)
    )
    has_projection_call = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "service"
        and node.func.attr == "identity_statistics"
        for node in ast.walk(tree)
    )

    assert has_statistics_branch
    assert has_projection_call
