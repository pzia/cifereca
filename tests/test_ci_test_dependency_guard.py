"""Covers fail-closed test-module consumer detection (#3920)."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci_test_dependency_guard import has_test_consumers


def _sample(tmp_path: Path, consumer: str) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tests / "test_consumer.py").write_text(consumer, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("consumer", [
    "from tests.test_fixture import VALUE\n",
    "import tests.test_fixture\n",
    "from .test_fixture import VALUE\n",
    "from tests import test_fixture\n",
    'importlib.import_module("tests.test_fixture")\n',
    'spec_from_file_location("foo", "tests/test_fixture.py")\n',
])
def test_import_consumers_force_full_selection(tmp_path, consumer):
    root = _sample(tmp_path, consumer)
    assert has_test_consumers(root, ["tests/test_fixture.py"])


@pytest.mark.parametrize("consumer", [
    'REFERENCE = "from tests.test_fixture import VALUE"\n',
    '# from tests.test_fixture import VALUE\n',
    'PATH = "tests/test_fixture.py"\n',
    'def test_unrelated(): pass\n',
])
def test_documentation_and_test_data_are_not_imports(tmp_path, consumer):
    root = _sample(tmp_path, consumer)
    assert not has_test_consumers(root, ["tests/test_fixture.py"])


def test_multifile_changes_and_plugin_consumers(tmp_path):
    root = _sample(tmp_path, "def test_unrelated(): pass\n")
    plugin_test = root / "src/plugins/sample/tests/test_plugin.py"
    plugin_test.parent.mkdir(parents=True)
    plugin_test.write_text("from tests.test_fixture import VALUE\n", encoding="utf-8")
    assert has_test_consumers(root, ["tests/test_other.py", "tests/test_fixture.py"])


def test_dynamic_namespace_import_falls_back(tmp_path):
    root = _sample(tmp_path, 'importlib.import_module(f"tests.{name}")\n')
    assert has_test_consumers(root, ["tests/test_fixture.py"])


def test_missing_test_module_selection_is_inert(tmp_path):
    root = _sample(tmp_path, "from tests.test_fixture import VALUE\n")
    assert not has_test_consumers(root, [])
    assert not has_test_consumers(root, ["src/cifereca/core.py"])


def test_unrelated_namespace_reference_does_not_parse_ast(tmp_path, monkeypatch):
    root = _sample(tmp_path, 'DATA = "tests.unrelated"\n')
    def unexpected_parse(*args, **kwargs):
        raise AssertionError("inert test namespace must not trigger AST parsing")
    monkeypatch.setattr("scripts.ci_test_dependency_guard.ast.parse", unexpected_parse)
    assert not has_test_consumers(root, ["tests/test_fixture.py"])


@pytest.mark.parametrize("consumer", [
    'importlib.import_module(f"tests.{name}")\n',
    '__import__(f"tests.{name}")\n',
    'spec_from_file_location("name", f"tests.{name}")\n',
    'SourceFileLoader("name", f"tests.{name}")\n',
])
def test_unknown_dynamic_namespace_still_forces_full(tmp_path, consumer):
    root = _sample(tmp_path, consumer)
    assert has_test_consumers(root, ["tests/test_fixture.py"])


@pytest.mark.parametrize("consumer", [
    'importlib.import_module("tests." + name)\n',
    '__import__("tests" + "." + module_name)\n',
    'spec_from_file_location("fixture", "tests/" + filename)\n',
    'SourceFileLoader("fixture", "tests/" + filename)\n',
    'spec_from_file_location("fixture", ROOT / "tests/test_fixture.py")\n',
])
def test_concatenated_dynamic_imports_fail_closed(tmp_path, consumer):
    root = _sample(tmp_path, consumer)
    assert has_test_consumers(root, ["tests/test_fixture.py"])


@pytest.mark.parametrize("consumer", [
    'pytest_plugins = ["tests.test_fixture"]\n',
    'pytest_plugins: tuple[str, ...] = ("tests.test_fixture",)\n',
    'pytest_plugins = ["tests." + name]\n',
    'pytest_plugins = dynamic_module_names\n',
])
def test_pytest_plugins_can_import_changed_test_module(tmp_path, consumer):
    root = _sample(tmp_path, consumer)
    assert has_test_consumers(root, ["tests/test_fixture.py"])


@pytest.mark.parametrize("consumer", [
    'pytest_plugins = ["tests.unrelated"]\n',
    'pytest_plugins = ["pytest_cov"]\n',
    'REFERENCE = "pytest_plugins = [tests.test_fixture]"\n',
    'importlib.import_module("json")\n',
    'spec_from_file_location("other", ROOT / "tests/test_other.py")\n',
    'spec_from_file_location("other", ROOT / "src/plugins/sample/tests/test_contract.py")\n',
    'importlib.import_module("tests.unrelated" + suffix)\n',
])
def test_unrelated_plugins_and_dynamic_imports_stay_targeted(tmp_path, consumer):
    root = _sample(tmp_path, consumer)
    assert not has_test_consumers(root, ["tests/test_fixture.py"])


@pytest.mark.parametrize("consumer", [
    'importlib.import_module(name)\nPREFIX = "tests."\nname = PREFIX + fixture_name\n',
    'PREFIX = "tests/"\nmodule = PREFIX + name\nspec_from_file_location("ref", module)\n',
    'PREFIX = "tests."\nname = PREFIX + fixture_name\n__import__(name)\n',
    'PREFIX = "tests."\nname = PREFIX + fixture_name\nSourceFileLoader("ref", name)\n',
])
def test_split_namespace_variable_dynamic_import_fails_closed(tmp_path, consumer):
    root = _sample(tmp_path, "def test_unrelated(): pass\n")
    consumer_path = root / "src/plugins/example/tests/test_dynamic.py"
    consumer_path.parent.mkdir(parents=True)
    consumer_path.write_text(consumer, encoding="utf-8")
    assert has_test_consumers(root, ["tests/test_fixture.py"])


@pytest.mark.parametrize("consumer", [
    'PREFIX = "tests.unrelated"\nmodule = PREFIX + suffix\nimportlib.import_module(module)\n',
    'PATH = "tests/test_other.py"\nmodule = PATH + suffix\nSourceFileLoader("other", module)\n',
    '# PREFIX = "tests."\nimportlib.import_module(name)\n',
])
def test_split_namespace_unrelated_imports_remain_targeted(tmp_path, consumer):
    root = _sample(tmp_path, consumer)
    assert not has_test_consumers(root, ["tests/test_fixture.py"])


@pytest.mark.parametrize("consumer", [
    'from importlib import import_module as load\nPREFIX = "tests."\nname = PREFIX + fixture_name\nload(name)\n',
    'from builtins import __import__ as load\nPREFIX = "tests."\nname = PREFIX + fixture_name\nload(name)\n',
    'from importlib.util import spec_from_file_location as load\nPREFIX = "tests/"\nname = PREFIX + fixture_name\nload("fixture", name)\n',
    'from importlib.machinery import SourceFileLoader as Load\nPREFIX = "tests/"\nname = PREFIX + fixture_name\nLoad("fixture", name)\n',
])
def test_aliased_dynamic_import_loaders_force_full_selection(tmp_path, consumer):
    root = _sample(tmp_path, "def test_unrelated(): pass\n")
    plugin = root / "src/plugins/fake/tests/test_consumer.py"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(consumer, encoding="utf-8")
    assert has_test_consumers(root, ["tests/test_fixture.py"])


def test_aliased_dynamic_import_unrelated_namespace_stays_targeted(tmp_path):
    root = _sample(
        tmp_path,
        'from importlib import import_module as load\n'
        'PREFIX = "tests.unrelated"\n'
        'name = PREFIX + suffix\n'
        'load(name)\n',
    )
    assert not has_test_consumers(root, ["tests/test_fixture.py"])


def test_unrelated_test_path_data_does_not_taint_dynamic_loader(tmp_path):
    root = _sample(
        tmp_path,
        'import importlib.util\n'
        'SELECTOR = "scripts/ci-select-core-tests.py"\n'
        'PATH = "tests/test_fixture.py"\n'
        'spec = importlib.util.spec_from_file_location("selector", SELECTOR)\n',
    )
    assert not has_test_consumers(root, ["tests/test_fixture.py"])


def test_chained_namespace_assignments_reach_dynamic_loader(tmp_path):
    root = _sample(
        tmp_path,
        'from importlib import import_module as load\n'
        'PREFIX = "tests."\n'
        'one = PREFIX + fixture_name\n'
        'two = one\n'
        'load(two)\n',
    )
    assert has_test_consumers(root, ["tests/test_fixture.py"])
