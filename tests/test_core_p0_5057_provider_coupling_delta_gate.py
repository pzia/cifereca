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


def test_new_callable_provider_routing_table_rejected_by_committed_git_gate(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(
        root, "src/cifereca/core/example.py",
        "def kobo_action(): return 1\n"
        "HANDLERS = {'kobo': kobo_action}\n",
    )
    result = gate(root, base, head)
    assert result["pass"] is False
    assert result["violations"] == {
        "src/cifereca/core/example.py": {"dispatch_tables:kobo": 1},
    }


def test_non_callable_provider_labels_do_not_trigger_new_gate(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(
        root, "src/cifereca/core/example.py",
        "DISPLAY_LABELS = {'kobo': 'Kobo', 'numilog': 'Numilog'}\n",
    )
    result = gate(root, base, head)
    assert result["pass"] is True


def test_dotted_tenant_and_underscore_provider_aliases_are_not_invisible():
    before = "pass\n"
    cases = (
        ("if source_id == 'biblioaccess.la-rochelle':\n    pass\n",
         ("comparisons", "biblioaccess.la-rochelle")),
        ("if source_id in {'provider-biblioaccess'}:\n    pass\n",
         ("comparisons", "provider-biblioaccess")),
        ("from . import biblioaccess_la_rochelle_actions\n",
         ("imports", "biblioaccess_la_rochelle")),
        ("from importlib import import_module as resolve\n"
         "resolve('cifereca.core.biblioaccess_adapter')\n",
         ("imports", "biblioaccess")),
    )
    for source, signal in cases:
        observed = added_signals(before, source)
        assert observed[signal] == 1
        # Prefix aliases may match the same imported module segment:
        # conservative double attribution is valid, never a missed gate.
        assert set(observed).issubset({signal, ("imports", "biblioaccess")})


def test_cross_file_relocation_is_detected_even_when_global_count_is_constant(
    tmp_path,
):
    root = tmp_path / "repo"
    base = _repo(root)
    _commit(root, "src/cifereca/core/second.py", "pass\n")
    base = _git(root, "rev-parse", "HEAD")
    (root / "src/cifereca/core/example.py").write_text("pass\n")
    (root / "src/cifereca/core/second.py").write_text(
        "if source == 'kobo':\n    pass\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "move")
    head = _git(root, "rev-parse", "HEAD")
    assert gate(root, base, head)["violations"] == {
        "src/cifereca/core/second.py": {"comparisons:kobo": 1},
    }


def test_cross_file_move_into_renamed_module_still_detects_new_provider_signal(
    tmp_path,
):
    root = tmp_path / "repo"
    base = _repo(root)
    _git(root, "mv", "src/cifereca/core/example.py",
         "src/cifereca/core/renamed.py")
    _git(root, "commit", "-m", "rename")
    head = _git(root, "rev-parse", "HEAD")
    # --no-renames deliberately treats this as an added destination. A naive
    # global total or rename-aware move could conceal a new executable owner.
    assert gate(root, base, head)["violations"] == {
        "src/cifereca/core/renamed.py": {"comparisons:kobo": 1},
    }


def test_new_alias_constant_and_dynamic_import_is_rejected_without_execution():
    source = (
        "NEW_PROVIDER = 'biblioaccess.la-rochelle'\n"
        "if account.source_provider == NEW_PROVIDER:\n    pass\n"
        "import importlib as loader\n"
        "loader.import_module('cifereca.biblioaccess.dispatch')\n"
    )
    assert added_signals("pass\n", source) == {
        ("comparisons", "biblioaccess.la-rochelle"): 1,
        ("imports", "biblioaccess"): 1,
    }


def test_git_gate_blocks_executable_provider_lookup_not_metadata(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(root, "src/cifereca/core/example.py",
                   'title = LABELS["kobo"]\n'
                   'handlers["bnf-pret"]()\n')
    result = gate(root, base, head)
    assert result["pass"] is False
    assert result["violations"] == {
        "src/cifereca/core/example.py": {"dispatch_calls:bnf-pret": 1},
    }


def test_provider_lookup_call_moved_across_lines_is_not_a_new_signal():
    before = 'handlers["kobo"]()\n'
    after = '\n' + before
    assert added_signals(before, after) == {}


def test_git_gate_blocks_provider_get_dispatch_but_not_noninvoked_get(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(root, "src/cifereca/core/example.py",
                   'label = translations.get("kobo")\n'
                   'result = actions.get("numilog")()\n')
    report = gate(root, base, head)
    assert report["pass"] is False
    assert report["violations"] == {
        "src/cifereca/core/example.py": {"dispatch_calls:numilog": 1},
    }


def test_git_gate_accepts_mapping_get_for_metadata_only(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(root, "src/cifereca/core/example.py",
                   'title = labels.get("kobo")\n')
    assert gate(root, base, head)["pass"] is True


def test_importlib_module_name_immutable_constant_is_not_an_ast_gate_bypass():
    source = (
        "from importlib import import_module as load\n"
        "MODULE_PATH = 'cifereca.biblioaccess.dispatch'\n"
        "load(MODULE_PATH)\n"
    )
    assert added_signals("pass\n", source) == {("imports", "biblioaccess"): 1}


def test_importlib_module_name_shadowed_or_computed_is_not_executed_or_inferred():
    cases = (
        "from importlib import import_module as load\n"
        "MODULE_PATH = 'cifereca.kobo.executor'\n"
        "MODULE_PATH = make_module_name()\n"
        "load(MODULE_PATH)\n",
        "from importlib import import_module as load\n"
        "MODULE_PATH = 'cifereca.kobo.executor'\n"
        "def run(MODULE_PATH): return load(MODULE_PATH)\n",
        "import importlib\n"
        "MODULE_PATH = prefix + '.kobo.executor'\n"
        "importlib.import_module(MODULE_PATH)\n",
        "MODULE_PATH = 'cifereca.kobo.executor'\n"
        "other_loader(MODULE_PATH)\n",
    )
    for source in cases:
        assert added_signals("pass\n", source) == {}


def test_git_gate_rejects_new_constant_based_dynamic_provider_import(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    head = _commit(root, "src/cifereca/core/example.py",
                   "import importlib as loader\n"
                   "PROVIDER_MODULE = 'cifereca.numilog.implementation'\n"
                   "loader.import_module(PROVIDER_MODULE)\n")
    report = gate(root, base, head)
    assert report["pass"] is False
    assert report["violations"] == {
        "src/cifereca/core/example.py": {"imports:numilog": 1},
    }


def test_immutable_provider_module_literal_not_used_for_import_is_inert():
    source = (
        "PROVIDER_MODULE = 'cifereca.biblioaccess.dispatch'\n"
        "module_description = PROVIDER_MODULE\n"
    )
    assert added_signals("pass\n", source) == {}


def test_full_biblioaccess_manifest_ids_cannot_hide_core_provider_dispatch():
    # Both real IDs come from src/plugins/provider-biblioaccess/cifereca-plugin.json.
    ids = (
        "provider.biblioaccess.la-rochelle",
        "provider.biblioaccess-catalogue.la-rochelle",
    )
    for provider_id in ids:
        assert added_signals(
            "pass\n", f"if source.provider_id == {provider_id!r}:\n    pass\n",
        ) == {("comparisons", provider_id): 1}
        assert added_signals(
            "pass\n", f"LABELS = {{ {provider_id!r}: 'Bibliothèque' }}\n",
        ) == {}  # Metadata alone is not executable dispatch.
        source = (
            "def handler(): return 1\n"
            f"HANDLERS = {{{provider_id!r}: handler}}\n"
        )
        assert added_signals("pass\n", source) == {
            ("dispatch_tables", provider_id): 1,
        }


def test_manifest_source_id_coupling_is_rejected_at_exact_git_head(tmp_path):
    root = tmp_path / "repo"
    base = _repo(root)
    full_id = "provider.biblioaccess-catalogue.la-rochelle"
    head = _commit(
        root, "src/cifereca/core/example.py",
        f"if source.provider_id == {full_id!r}:\n    pass\n",
    )
    report = gate(root, base, head)
    assert report["pass"] is False
    assert report["violations"] == {
        "src/cifereca/core/example.py": {
            f"comparisons:{full_id}": 1,
        },
    }
