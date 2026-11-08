"""P0 #5057: executable regression fixtures for monotone AST coupling gate."""
from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.ci_provider_coupling_delta_gate import added_signals, gate, main


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def _commit(root: Path, path: str, content: str) -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(root, "add", path)
    _git(root, "commit", "-m", "fixture")
    return _git(root, "rev-parse", "HEAD")


def _repo(root: Path) -> str:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "CI Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    return _commit(root, "src/cifereca/core/example.py",
                   "if source == 'kobo':\n    pass\n")


def test_added_signals_per_category_and_provider_not_global_net_totals():
    before = ("if source == 'kobo':\n    pass\n"
              "if source == 'numilog':\n    pass\n")
    after = ("if source == 'kobo':\n    pass\n"
             "if source == 'bnf-pret':\n    pass\n")
    assert added_signals(before, after) == {("comparisons", "bnf-pret"): 1}
    assert added_signals(before, before) == {}


def test_moving_line_numbers_does_not_count_as_new_coupling():
    before = "from cifereca.kobo.executor import run\n"
    after = "\n\n" + before
    assert added_signals(before, after) == {}


def test_added_provider_import_and_membership_are_blocked():
    before = "value = 1\n"
    after = ("from .core import numilog_owned_download\n"
             "if source in {'kobo', 'bnf-pret'}:\n    pass\n")
    assert added_signals(before, after) == {
        ("imports", "numilog"): 1, ("comparisons", "bnf-pret"): 1,
        ("comparisons", "kobo"): 1,
    }


def test_git_gate_rejects_added_core_coupling_even_when_other_removed(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(root, "src/cifereca/core/example.py",
                   "if source == 'bnf-pret':\n    pass\n")
    report = gate(root, base, head)
    assert report["pass"] is False
    assert report["changed_core_python_files"] == 1
    assert report["violations"] == {
        "src/cifereca/core/example.py": {"comparisons:bnf-pret": 1},
    }
    assert main(["--root", str(root), "--base-ref", base,
                 "--head-ref", head]) == 1


def test_new_core_file_blocked_but_external_plugin_ignored(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    _commit(root, "src/plugins/provider-kobo/foo.py",
            "if source == 'kobo':\n    pass\n")
    head = _commit(root, "src/cifereca/core/new.py",
                   "from ..core import bnf_explicit_acsm\n")
    report = gate(root, base, head)
    assert report["violations"] == {
        "src/cifereca/core/new.py": {"imports:bnf": 1},
    }
    assert report["changed_core_python_files"] == 1


def test_unchanged_and_reduced_debt_is_admitted(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(root, "src/cifereca/core/example.py", "value = 42\n")
    assert gate(root, base, head)["pass"] is True
    assert main(["--root", str(root), "--base-ref", base,
                 "--head-ref", head]) == 0


def test_syntax_error_and_unavailable_revision_fail_closed(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(root, "src/cifereca/core/example.py", "if (\n")
    report = gate(root, base, head)
    assert report["parse_errors"] == ["src/cifereca/core/example.py"]
    assert report["pass"] is False
    assert main(["--root", str(root), "--base-ref", "invalid",
                 "--head-ref", head]) == 1


def test_deleting_core_file_does_not_reintroduce_coupling(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    (root / "src/cifereca/core/example.py").unlink()
    _git(root, "add", "-u")
    _git(root, "commit", "-m", "delete")
    head = _git(root, "rev-parse", "HEAD")
    report = gate(root, base, head)
    assert report["pass"] is True
    assert report["changed_core_python_files"] == 0
