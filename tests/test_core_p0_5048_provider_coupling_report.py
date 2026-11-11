"""Regression coverage for the explanatory provider/Core LOC report."""
from pathlib import Path

from scripts.ci_provider_core_coupling_report import extended_inventory


def write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_disjoint_partition_and_passive_mentions(tmp_path):
    write(tmp_path, "src/cifereca/core/kobo_backup.py", "x = 1\n# kobo\n")
    write(tmp_path, "src/cifereca/core/router.py",
          '"""Kobo reference."""\n'
          '# BnF documentation\n'
          'if provider_id == "numilog":\n'
          '    call()\n'
          'LABEL = "Paris"\n'
          'item = 1  # Kobo help\n'
          '\n'
          'nothing = 1\n')
    write(tmp_path, "src/plugins/provider-kobo/driver.py",
          "if provider == 'kobo': pass\n")
    r = extended_inventory(tmp_path)
    p = r["line_partition"]
    assert r["historical"]["dedicated_lines"] == 2
    assert r["historical"]["generic_provider_literal_lines_unique"] == 4
    assert r["total_core_python_physical_lines"] == 10
    assert p["dedicated_modules"] == 2
    assert p["generic_ast_signal"] == 1
    assert p["generic_passive_code_mentions"] == 1
    assert p["generic_passive_docstrings"] == 1
    assert p["generic_passive_comments"] == 2
    assert sum(p.values()) == r["total_core_python_physical_lines"]


def test_vendor_named_module_outside_legacy_scope_not_hidden(tmp_path):
    write(tmp_path, "src/cifereca/gui/bnf_lending_controller.py",
          "def draw():\n    return 4\n")
    r = extended_inventory(tmp_path)
    assert r["historical"]["dedicated_lines"] == 0
    assert "src/cifereca/gui/bnf_lending_controller.py" in (
        r["vendor_named_modules_outside_legacy_dedicated_scope"])
    assert r["total_core_python_physical_lines"] == 2


def test_substring_noise_is_not_ast_dispatch(tmp_path):
    write(tmp_path, "src/cifereca/services.py",
          "comparison = 1\n"
          "constant = 'comparison'\n"
          "# paris text\n")
    r = extended_inventory(tmp_path)
    assert r["line_partition"]["generic_ast_signal"] == 0
    assert r["line_partition"]["generic_passive_code_mentions"] == 0
    assert r["line_partition"]["generic_passive_comments"] == 1
    assert r["historical"]["generic_provider_literal_lines_unique"] == 3


def test_empty_repo_is_safe(tmp_path):
    result = extended_inventory(tmp_path)
    assert result["total_core_python_physical_lines"] == 0
    assert all(n == 0 for n in result["line_partition"].values())
    assert list(tmp_path.iterdir()) == []
