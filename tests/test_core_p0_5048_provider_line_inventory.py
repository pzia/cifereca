"""P0 #5048: physical provider LOC audit is reproducible and non-destructive."""
from pathlib import Path

from scripts.ci_kobo_core_line_inventory import inventory as kobo_inventory
from scripts.ci_provider_core_line_inventory import inventory, main


def _write(root, name, content):
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")
    return file


def test_named_implementation_files_counted_without_plugins_or_tests(tmp_path):
    _write(tmp_path, "src/cifereca/core/kobo_backup.py", "a = 1\n# comment\n\n")
    _write(tmp_path, "src/cifereca/core/numilog_direct.py", "x = 1\ny = 2\n")
    _write(tmp_path, "src/cifereca/core/bnf_explicit.py", "z = 3\n")
    _write(tmp_path, "src/cifereca/kobo/executor.py", "w = 4\n")
    _write(tmp_path, "src/cifereca/gui/legacy_kobo_view.py", "w = 5\n")
    _write(tmp_path, "src/cifereca/core/generic.py",
           "if provider == 'kobo': pass\n"
           "if provider == 'numilog': pass\n"
           "label = 'bnf + kobo'\n")
    _write(tmp_path, "src/plugins/provider-kobo/acquire.py", "a = 7\n")
    _write(tmp_path, "tests/test_kobo.py", "assert True\n")
    report = inventory(tmp_path)
    assert report["dedicated_lines"] == 7
    assert report["by_provider"]["kobo"] == 4
    assert report["by_provider"]["numilog"] == 2
    assert report["by_provider"]["bnf"] == 1
    assert report["generic_provider_literal_lines_unique"] == 3
    assert report["generic_per_file"]["src/cifereca/core/generic.py"]["provider_literal_lines"] == {
        "kobo": 2, "numilog": 1, "bnf": 1, "biblioaccess": 0,
    }
    assert report["dedicated_files"] == 5


def test_empty_tree_and_read_only_cli(tmp_path, capsys):
    assert inventory(tmp_path)["dedicated_lines"] == 0
    assert main(["--root", str(tmp_path), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"dedicated_lines": 0' in out
    assert list(tmp_path.iterdir()) == []


def test_kobo_dedicated_scope_matches_existing_inventory(tmp_path):
    _write(tmp_path, "src/cifereca/core/kobo_backup.py", "a = 1\n")
    _write(tmp_path, "src/cifereca/kobo/executor.py", "a = 2\n")
    _write(tmp_path, "src/cifereca/gui/legacy_kobo_view.py", "a = 3\n")
    _write(tmp_path, "src/cifereca/core/not_kobo.py", "a = 4\n")
    assert inventory(tmp_path)["by_provider"]["kobo"] == kobo_inventory(tmp_path)["dedicated_lines"]


def test_filenames_without_provider_name_are_generic(tmp_path):
    _write(tmp_path, "src/cifereca/services.py", "LABELS = {'kobo': 'Kobo'}\n")
    _write(tmp_path, "src/cifereca/core/search_orchestration.py",
           "if provider_id == 'bnf-pret': pass\n")
    report = inventory(tmp_path)
    assert report["dedicated_lines"] == 0
    assert report["generic_files_with_provider_literal"] == 2
    assert report["generic_provider_literal_lines_unique"] == 2
