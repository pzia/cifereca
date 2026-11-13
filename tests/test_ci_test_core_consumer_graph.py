"""Focused source-only inventory contracts for #5376, without executing plugins."""
from __future__ import annotations

from pathlib import Path

from scripts.ci_test_core_consumer_graph import _imports, inventory


def _write(root: Path, path: str, source: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


def test_plugin_direct_and_transitive_root_test_graph(tmp_path):
    _write(tmp_path, "tests/test_a.py", "from tests.test_b import fixture\n")
    _write(tmp_path, "tests/test_b.py", "from tests.test_c import fixture\n")
    _write(tmp_path, "tests/test_c.py", "fixture = 1\n")
    _write(tmp_path, "src/plugins/provider-x/tests/test_client.py",
           "from tests.test_a import fixture\n")
    graph = inventory(tmp_path)
    assert graph["parse_errors"] == []
    assert graph["plugin_test_files"] == 1
    assert [(e["owner"], e["target_module"], e["resolved"])
            for e in graph["plugin_to_root_direct"]] == [
                ("plugin:provider-x", "tests.test_a", True)
            ]
    assert graph["plugin_to_root_transitive"] == [
        {"consumer": "src/plugins/provider-x/tests/test_client.py",
         "target": "tests/test_b.py"},
        {"consumer": "src/plugins/provider-x/tests/test_client.py",
         "target": "tests/test_c.py"},
    ]


def test_qualified_relative_and_pytest_plugin_imports():
    source = ('from .test_b import value\n'
              'from tests import test_c\n'
              'pytest_plugins = ["tests.test_d"]\n'
              'import tests.test_e\n')
    imports, unknown = _imports(source, is_root_test=True)
    assert [(target, kind) for target, kind, _ in imports] == [
        ("tests.test_b", "relative"),
        ("tests.test_c", "from_namespace"),
        ("tests.test_d", "pytest_plugins"),
        ("tests.test_e", "import"),
    ]
    assert unknown == []


def test_dynamic_literal_and_alias_file_loader():
    source = ('from importlib.machinery import SourceFileLoader as Load\n'
              'Load("fixture", "tests/test_fixture.py")\n'
              'from importlib import import_module as load\n'
              'load("tests.test_second")\n')
    found, unresolved = _imports(source, is_root_test=False)
    assert [module for module, _, _ in found] == [
        "tests.test_fixture", "tests.test_second",
    ]
    assert unresolved == []


def test_unresolved_dynamic_is_not_attested_as_proven_edge(tmp_path):
    _write(tmp_path, "tests/test_fixture.py", "VALUE=1\n")
    _write(tmp_path, "src/plugins/a/tests/test_a.py",
           'from importlib import import_module as load\n'
           'prefix = "tests."\n'
           'load(prefix + name)\n'
           'pytest_plugins = generated_plugins\n'
           'DATA = "from tests.test_fixture import VALUE"\n')
    graph = inventory(tmp_path)
    assert graph["plugin_to_root_direct"] == []
    assert len(graph["unresolved_dynamic_or_pytest_plugins"]) == 2


def test_missing_target_and_parse_errors_are_visible(tmp_path):
    _write(tmp_path, "tests/test_existing.py", "VALUE=1\n")
    _write(tmp_path, "src/plugins/a/tests/test_a.py",
           "from tests.test_missing import fixture\n")
    _write(tmp_path, "src/plugins/b/tests/test_b.py", "def broken(:\n")
    graph = inventory(tmp_path)
    assert graph["plugin_to_root_direct"][0]["resolved"] is False
    assert graph["plugin_to_root_direct"][0]["target"] is None
    assert graph["parse_errors"] == [
        {"consumer": "src/plugins/b/tests/test_b.py", "reason": "SyntaxError"}
    ]


def test_cycle_is_bounded_and_deterministic(tmp_path):
    _write(tmp_path, "tests/test_a.py", "from tests.test_b import B\n")
    _write(tmp_path, "tests/test_b.py", "from tests.test_a import A\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py",
           "from tests.test_a import A\n")
    graph = inventory(tmp_path)
    assert graph["plugin_to_root_transitive"] == [
        {"consumer": "src/plugins/p/tests/test_p.py", "target": "tests/test_b.py"}
    ]
    assert inventory(tmp_path) == graph
