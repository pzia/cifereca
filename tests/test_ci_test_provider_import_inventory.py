"""#5376 provider test coupling inventory: real plugin source ownership only."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci_test_provider_import_inventory import (
    inspect_source, inventory, modules_by_provider,
)


def _write(root: Path, path: str, value: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def _root(tmp_path: Path) -> Path:
    _write(tmp_path, "src/plugins/provider-paris/cifereca_paris.py",
           "def provide(): pass\n")
    _write(tmp_path, "src/plugins/provider-kobo/cifereca_kobo.py",
           "def provide(): pass\n")
    _write(tmp_path, "src/plugins/provider-kobo/cifereca_kobo_catalog.py",
           "def search(): pass\n")
    _write(tmp_path, "src/plugins/diagnostics-page/diagnostics_ui.py",
           "def fake(): pass\n")
    return tmp_path


def test_exact_plugin_modules_are_attributed_without_filename_inference(tmp_path):
    root = _root(tmp_path)
    known = modules_by_provider(root)
    assert set(known) == {
        "cifereca_kobo", "cifereca_kobo_catalog", "cifereca_paris",
    }
    _write(root, "tests/test_custom.py", (
        "import cifereca_paris\n"
        "import cifereca_kobo_catalog as catalogue\n"
        "from cifereca_kobo import _legacy, public\n"
        "from tests.test_public_helper import private_fixture\n"
        "VALUE = 'from cifereca_paris import forbidden'\n"
    ))
    result = inventory(root)
    assert result["root_test_modules_scanned"] == 1
    assert result["provider_modules_indexed"] == 3
    assert result["root_test_modules_with_direct_provider_imports"] == 1
    assert result["direct_import_edges"] == 4
    assert result["private_symbol_edges"] == 1
    assert result["by_provider"] == {
        "provider-kobo": 3,
        "provider-paris": 1,
    }
    assert result["parse_or_io_errors"] == []
    assert {x["module"] for x in result["edges"]} == {
        "cifereca_paris", "cifereca_kobo_catalog", "cifereca_kobo",
    }


def test_dynamic_literal_is_proven_but_variable_import_stays_unresolved(tmp_path):
    root = _root(tmp_path)
    _write(root, "tests/sub/test_dynamic.py", (
        "from importlib import import_module as load\n"
        "import importlib\n"
        "from pytest import importorskip\n"
        'load("cifereca_paris")\n'
        'importlib.import_module("cifereca_kobo")\n'
        'importorskip("cifereca_kobo_catalog")\n'
        "load(module_name)\n"
        "ALIAS = 'cifereca_paris'\n"
        "fake = lambda module: module.import_module('cifereca_paris')\n"
    ))
    report = inventory(root)
    assert report["direct_import_edges"] == 3
    assert all(x["mechanism"] == "dynamic_literal" for x in report["edges"])
    assert report["unresolved_dynamic_import_calls"] == [
        {"test": "tests/sub/test_dynamic.py", "line": 7}
    ]
    assert report["root_test_modules_scanned"] == 1


def test_ambiguous_module_ownership_does_not_claim_a_provider(tmp_path):
    root = _root(tmp_path)
    _write(root, "src/plugins/provider-extra/cifereca_paris.py", "")
    _write(root, "tests/test_case.py", "from cifereca_paris import _private\n")
    report = inventory(root)
    assert report["direct_import_edges"] == 1
    assert report["ambiguous_owner_edges"] == 1
    assert report["private_symbol_edges"] == 1
    assert report["edges"][0]["provider"] is None
    assert report["by_provider"] == {"ambiguous": 1}


def test_incomplete_root_tree_reports_parsing_errors_not_success(tmp_path):
    root = _root(tmp_path)
    _write(root, "tests/test_valid.py", "import cifereca_paris\n")
    _write(root, "tests/test_syntax.py", "def broken(:\n")
    report = inventory(root)
    assert report["direct_import_edges"] == 1
    assert report["parse_or_io_errors"] == [
        {"test": "tests/test_syntax.py", "reason": "SyntaxError"}
    ]


def test_duplicate_imports_do_not_invent_test_cases(tmp_path):
    root = _root(tmp_path)
    _write(root, "tests/test_first.py", (
        "import cifereca_paris\n"
        "import cifereca_paris\n"
    ))
    _write(root, "tests/test_second.py", "from cifereca_paris import provide\n")
    report = inventory(root)
    assert report["root_test_modules_with_direct_provider_imports"] == 2
    assert report["direct_import_edges"] == 3
    assert [x["line"] for x in report["edges"]] == [1, 2, 1]


def test_ignored_unrelated_plugin_import_is_not_counted(tmp_path):
    root = _root(tmp_path)
    _write(root, "tests/test_other.py", (
        "from diagnostics_ui import fake\n"
        "from cifereca.services import LibraryService\n"
        "from tests.test_other_helper import fixture\n"
    ))
    report = inventory(root)
    assert report["direct_import_edges"] == 0
    assert report["root_test_modules_with_direct_provider_imports"] == 0
