"""Regression tests for P0 #5048 provider coupling inventory."""
from __future__ import annotations

from pathlib import Path

from scripts.ci_provider_core_coupling_inventory import inspect_source, inventory


def test_static_imports_and_comparisons_are_located_without_execution():
    source = """
from cifereca.kobo.executor import run
import cifereca.bnf.reader
provider_id = "kobo"
if provider_id == "kobo":
    pass
match provider_id:
    case "numilog":
        pass
"""
    result = inspect_source(source)
    assert len(result["imports"]) == 2
    assert [x["providers"] for x in result["imports"]] == [["kobo"], ["bnf"]]
    assert [x["providers"] for x in result["comparisons"]] == [["kobo"], ["numilog"]]


def test_inventory_excludes_plugin_and_test_sources(tmp_path: Path):
    base = tmp_path / "src"
    core = base / "cifereca" / "core"
    plugin = base / "plugins" / "provider-kobo"
    core.mkdir(parents=True)
    plugin.mkdir(parents=True)
    (core / "example.py").write_text('if provider_id == "kobo":\n    pass\n', encoding="utf-8")
    (plugin / "example.py").write_text('if provider_id == "kobo":\n    pass\n', encoding="utf-8")
    result = inventory(tmp_path)
    assert result["files_with_signals"] == 1
    assert result["comparison_signals"] == 1
    assert result["import_signals"] == 0
    assert list(result["findings"]) == ["src/cifereca/core/example.py"]


def test_absent_source_tree_is_valid_and_does_not_write(tmp_path: Path):
    result = inventory(tmp_path)
    assert result["files_with_signals"] == 0
    assert result["parse_errors"] == {}
    assert list(tmp_path.iterdir()) == []


def test_bad_python_is_reported_not_silently_ignored(tmp_path: Path):
    core = tmp_path / "src" / "cifereca" / "core"
    core.mkdir(parents=True)
    (core / "broken.py").write_text("if (\n", encoding="utf-8")
    result = inventory(tmp_path)
    assert result["parse_errors"] == {"src/cifereca/core/broken.py": "SyntaxError"}


def test_relative_implementation_imports_and_root_modules_are_included(tmp_path: Path):
    from scripts.ci_provider_core_coupling_inventory import inspect_source

    result = inspect_source("from .core import kobo_backup\\nfrom . import bnf_reader\\n")
    assert len(result["imports"]) == 2
    assert result["imports"][0]["providers"] == ["kobo"]
    assert result["imports"][1]["providers"] == ["bnf"]

    package = tmp_path / "src" / "cifereca"
    package.mkdir(parents=True)
    (package / "brokers.py").write_text('if provider_id == "numilog":\\n    pass\\n', encoding="utf-8")
    report = inventory(tmp_path)
    assert report["files_with_signals"] == 1
    assert list(report["findings"]) == ["src/cifereca/brokers.py"]
