"""Focused source-only inventory contracts for #5376, without executing plugins."""
from __future__ import annotations

from pathlib import Path

from scripts.ci_test_core_consumer_graph import _imports, inventory, plugin_replay_required


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
    assert graph["plugin_missing_imports"][0]["resolved"] is False
    assert graph["plugin_missing_imports"][0]["target"] is None
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


def test_plugin_test_imports_root_fixture_through_local_helper(tmp_path):
    _write(tmp_path, "tests/test_fixture.py", "VALUE = 1\n")
    _write(tmp_path, "tests/test_dep.py", "from tests.test_fixture import VALUE\n")
    _write(tmp_path, "src/plugins/alpha/tests/helpers.py",
           "from tests.test_dep import VALUE\n")
    _write(tmp_path, "src/plugins/alpha/tests/test_client.py",
           "from .helpers import VALUE\n")
    graph = inventory(tmp_path)
    consumer = "src/plugins/alpha/tests/test_client.py"
    assert graph["plugin_to_root_direct"] == [
        {"consumer": "src/plugins/alpha/tests/helpers.py",
         "owner": "plugin:alpha", "target_module": "tests.test_dep",
         "target": "tests/test_dep.py", "mechanism": "from", "line": 1,
         "resolved": True},
    ]
    assert graph["plugin_to_root_via_helpers"] == [
        {"consumer": consumer, "target": "tests/test_dep.py"},
        {"consumer": consumer, "target": "tests/test_fixture.py"},
    ]


def test_plugin_helper_cycles_and_cross_provider_are_isolated(tmp_path):
    _write(tmp_path, "tests/test_fixture.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/alpha/tests/helpers.py",
           "from . import second\nfrom tests.test_fixture import VALUE\n")
    _write(tmp_path, "src/plugins/alpha/tests/second.py",
           "from . import helpers\n")
    _write(tmp_path, "src/plugins/alpha/tests/test_client.py",
           "from . import helpers\n")
    _write(tmp_path, "src/plugins/beta/tests/test_client.py",
           "from . import helpers\n")
    graph = inventory(tmp_path)
    assert graph["parse_errors"] == []
    assert {"consumer": "src/plugins/alpha/tests/test_client.py",
            "target": "tests/test_fixture.py"} in graph["plugin_to_root_via_helpers"]
    assert not any(
        entry["consumer"].startswith("src/plugins/beta/")
        for entry in graph["plugin_to_root_via_helpers"]
    )


def test_non_relative_tests_import_is_not_assumed_plugin_helper(tmp_path):
    _write(tmp_path, "tests/test_fixture.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/alpha/tests/helpers.py",
           "from tests.test_fixture import VALUE\n")
    _write(tmp_path, "src/plugins/alpha/tests/test_client.py",
           "from tests.helpers import VALUE\n")
    graph = inventory(tmp_path)
    assert graph["plugin_to_plugin_helper_direct"] == []
    assert graph["plugin_to_root_via_helpers"] == []


def test_nested_relative_helper_chain_is_bounded(tmp_path):
    _write(tmp_path, "tests/test_fixture.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/alpha/tests/helpers.py",
           "from tests.test_fixture import VALUE\n")
    _write(tmp_path, "src/plugins/alpha/tests/nested/test_client.py",
           "from ..helpers import VALUE\n")
    graph = inventory(tmp_path)
    assert {"consumer": "src/plugins/alpha/tests/nested/test_client.py",
            "target": "tests/test_fixture.py"} in graph["plugin_to_root_via_helpers"]
    assert graph == inventory(tmp_path)


def test_plugin_package_imports_are_distinct_from_core_dependencies(tmp_path):
    _write(tmp_path, "tests/test_core.py", "VALUE=1\n")
    _write(tmp_path, "tests/test_shared_name.py", "VALUE=2\n")
    prefix = "src/plugins/provider-x/tests/"
    _write(tmp_path, prefix + "test_local.py", "VALUE=3\n")
    _write(tmp_path, prefix + "test_shared_name.py", "VALUE=4\n")
    _write(tmp_path, prefix + "test_consumer.py",
           "from tests.test_local import VALUE\n"
           "from tests.test_core import VALUE\n"
           "from tests.test_shared_name import VALUE\n"
           "from tests.test_missing import VALUE\n")
    graph = inventory(tmp_path)
    assert [e["target"] for e in graph["plugin_to_root_direct"]] == ["tests/test_core.py"]
    assert [e["target"] for e in graph["plugin_local_imports"]] == [prefix + "test_local.py"]
    assert [e["local_target"] for e in graph["plugin_ambiguous_imports"]] == [
        prefix + "test_shared_name.py"
    ]
    assert [e["target_module"] for e in graph["plugin_missing_imports"]] == ["tests.test_missing"]

def test_graph_detects_pytest_and_runpy_loaders_and_aliases():
    source = (
        'import pytest\npytest.importorskip("tests.test_a")\n'
        'from runpy import run_module as execute\nexecute("tests.test_b")\n'
        'import runpy\nrunpy.run_path("tests/test_c.py")\n'
    )
    found, unknown = _imports(source, is_root_test=False)
    assert [module for module, _, _ in found] == [
        "tests.test_a", "tests.test_b", "tests.test_c",
    ]
    assert unknown == []


def test_plugin_replay_follows_root_fixture_chain(tmp_path):
    _write(tmp_path, "tests/test_a.py", "from tests.test_b import VALUE\n")
    _write(tmp_path, "tests/test_b.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py", "from tests.test_a import VALUE\n")
    assert plugin_replay_required(["tests/test_b.py"], tmp_path)
    assert not plugin_replay_required(["tests/test_unrelated.py"], tmp_path)


def test_plugin_replay_follows_plugin_local_helper(tmp_path):
    _write(tmp_path, "tests/test_a.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/p/tests/helpers.py", "from tests.test_a import VALUE\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py", "from .helpers import VALUE\n")
    assert plugin_replay_required(["tests/test_a.py"], tmp_path)


def test_plugin_replay_detects_dynamic_root_loader_in_plugin(tmp_path):
    _write(tmp_path, "tests/test_a.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py",
           'from pytest import importorskip as load\n'
           'load(f"tests.{name}")\n')
    assert plugin_replay_required(["tests/test_a.py"], tmp_path)


def test_plugin_replay_fails_closed_for_root_fixture_dynamic_import(tmp_path):
    _write(tmp_path, "tests/test_a.py",
           "from importlib import import_module\nimport_module(variable)\n")
    _write(tmp_path, "tests/test_b.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py", "from tests.test_a import VALUE\n")
    assert plugin_replay_required(["tests/test_b.py"], tmp_path)


def test_plugin_replay_fails_closed_for_plugin_parse_error(tmp_path):
    _write(tmp_path, "tests/test_a.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py", "def broken(:\n")
    assert plugin_replay_required(["tests/test_a.py"], tmp_path)


def test_unrelated_and_non_root_changes_keep_plugins_targeted(tmp_path):
    _write(tmp_path, "tests/test_a.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py", "from tests import test_other\n")
    assert not plugin_replay_required(["tests/test_a.py"], tmp_path)
    assert not plugin_replay_required(["src/cifereca/services.py"], tmp_path)


def test_plugin_replay_flags_ambiguous_local_namespace(tmp_path):
    _write(tmp_path, "tests/test_shared.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/p/tests/test_shared.py", "VALUE = 2\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py", "from tests.test_shared import VALUE\n")
    assert plugin_replay_required(["tests/test_shared.py"], tmp_path)
