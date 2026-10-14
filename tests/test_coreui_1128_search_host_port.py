"""Search controller dependencies are declared without GTK imports (#1128)."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def test_ports_are_headless_and_do_not_import_main_window():
    source = (ROOT / "search_host_port.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("gi" in ast.unparse(node) or "main_window" in ast.unparse(node) for node in imports)
    names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert names == {"SearchControllerHost", "SearchDetailHost"}


def test_search_adapter_functions_bind_to_declared_ports():
    for filename, expected in (("search_controller.py", "SearchControllerHost"),
                               ("search_details.py", "SearchDetailHost")):
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        assert functions
        assert all(node.args.args[0].arg == "self" for node in functions)
        assert all(isinstance(node.args.args[0].annotation, ast.Name)
                   and node.args.args[0].annotation.id == expected for node in functions)


def test_search_ports_contain_explicit_lifecycle_and_context_interfaces():
    tree = ast.parse((ROOT / "search_host_port.py").read_text(encoding="utf-8"))
    ports = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    controller = ast.unparse(ports["SearchControllerHost"])
    detail = ast.unparse(ports["SearchDetailHost"])
    assert "runner:" in controller and "service:" in controller
    assert "_search_generation:" in controller
    assert "_search_detail_request_generation:" in detail
    assert "_identity_context_generation:" in detail
    assert "_search_detail_context_current" in detail
