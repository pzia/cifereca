"""P0 Kobo AST inventory: physical-line uniqueness and provider separation."""
from __future__ import annotations

from scripts.ci_kobo_ast_focus import inventory, main


def _source(root, path, body):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def test_kobo_ast_worklist_is_unique_and_excludes_named_files(tmp_path):
    _source(tmp_path, "src/cifereca/services.py",
            "if provider_id == 'kobo':\n"
            "    service.sync_kobo_backup()\n"
            "if provider_id == 'numilog':\n"
            "    pass\n"
            "note = 'kobo'\n"
            "# if provider_id == 'kobo': pass\n")
    _source(tmp_path, "src/cifereca/core/kobo_backup.py",
            "if provider_id == 'kobo': pass\n")
    _source(tmp_path, "src/plugins/provider-kobo/provider.py",
            "if provider_id == 'kobo': pass\n")
    result = inventory(tmp_path)
    assert result["unique_signal_lines"] == 2
    assert result["files_with_signals"] == 1
    assert [(row["path"], row["line"]) for row in result["findings"]] == [
        ("src/cifereca/services.py", 1),
        ("src/cifereca/services.py", 2),
    ]
    assert result["parse_errors"] == {}


def test_empty_and_invalid_source_fail_closed(tmp_path, capsys):
    assert inventory(tmp_path)["unique_signal_lines"] == 0
    _source(tmp_path, "src/cifereca/broken.py", "if (\n")
    result = inventory(tmp_path)
    assert result["parse_errors"] == {"src/cifereca/broken.py": "SyntaxError"}
    assert main(["--root", str(tmp_path), "--json"]) == 1
    assert '"parse_errors"' in capsys.readouterr().out
