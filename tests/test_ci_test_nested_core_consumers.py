"""#5376: nested root pytest modules must not silently escape the CI graph."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from scripts.ci_test_core_consumer_graph import _imports, inventory, plugin_replay_required

ROOT = Path(__file__).resolve().parents[1]


def _write(root: Path, path: str, source: str) -> None:
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(source, encoding="utf-8")


def test_nested_root_import_ast_tracks_relative_package_and_parent_levels():
    source = (
        "from .test_sibling import VALUE\n"
        "from ..test_top import VALUE\n"
        "from tests.units import test_other\n"
    )
    found, unresolved = _imports(
        source, is_root_test=True, consumer_module="tests.units.test_a",
    )
    assert [(module, method) for module, method, _ in found] == [
        ("tests.test_top", "relative"),
        ("tests.units.test_other", "from_namespace"),
        ("tests.units.test_sibling", "relative"),
    ]
    assert unresolved == []


def test_nested_root_tests_are_in_inventory_with_transitive_plugin_consumers(tmp_path):
    _write(tmp_path, "tests/test_top.py", "VALUE = 1\n")
    _write(tmp_path, "tests/units/test_b.py", "VALUE = 2\n")
    _write(tmp_path, "tests/units/test_a.py",
           "from ..test_top import VALUE\n"
           "from .test_b import VALUE\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py",
           "from tests.units.test_a import VALUE\n")
    graph = inventory(tmp_path)
    consumer = "src/plugins/p/tests/test_p.py"
    assert graph["root_test_files"] == 3
    assert graph["parse_errors"] == []
    assert [(e["target_module"], e["target"], e["resolved"])
            for e in graph["plugin_to_root_direct"]] == [
        ("tests.units.test_a", "tests/units/test_a.py", True),
    ]
    assert graph["plugin_to_root_transitive"] == [
        {"consumer": consumer, "target": "tests/test_top.py"},
        {"consumer": consumer, "target": "tests/units/test_b.py"},
    ]


def test_nested_same_named_local_plugin_test_is_ambiguous_not_attested(tmp_path):
    _write(tmp_path, "tests/units/test_shared.py", "VALUE = 'root'\n")
    _write(tmp_path, "src/plugins/p/tests/units/test_shared.py",
           "VALUE = 'local'\n")
    _write(tmp_path, "src/plugins/p/tests/test_client.py",
           "from tests.units.test_shared import VALUE\n")
    graph = inventory(tmp_path)
    assert graph["plugin_to_root_direct"] == []
    assert graph["plugin_ambiguous_imports"] == [{
        "consumer": "src/plugins/p/tests/test_client.py",
        "owner": "plugin:p", "target_module": "tests.units.test_shared",
        "target": "tests/units/test_shared.py",
        "mechanism": "from", "line": 1, "resolved": True,
        "local_target": "src/plugins/p/tests/units/test_shared.py",
    }]


def test_nested_root_changes_replay_plugins_without_claiming_exact_namespace(tmp_path):
    _write(tmp_path, "tests/units/test_new.py", "VALUE = 1\n")
    _write(tmp_path, "src/plugins/p/tests/test_p.py", "VALUE = 2\n")
    # Conservative until import-mode/fixture provenance has been attested.
    assert plugin_replay_required(["tests/units/test_new.py"], tmp_path)
    assert not plugin_replay_required(["tests/units/helper.py"], tmp_path)


def test_core_selector_runs_full_for_nested_test_change(tmp_path):
    _write(tmp_path, "tests/units/test_new.py", "VALUE = 1\n")
    script = ROOT / "scripts/ci-select-core-tests.py"
    spec = importlib.util.spec_from_file_location("core_nested_test_selector", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.select(["tests/units/test_new.py"], tmp_path) == ("full", ["tests"])


def test_pr_ci_routes_nested_test_changes_and_deletions_to_plugin_replay():
    workflow = (ROOT / ".github/workflows/pr-ci.yml").read_text(encoding="utf-8")
    expression = "tests/(.*/)?test_.*\\.py$"
    assert workflow.count(expression) == 2
    assert "ci_test_core_consumer_graph.py --plugin-replay-changed" in workflow
    assert "git diff --name-status --no-renames" in workflow
