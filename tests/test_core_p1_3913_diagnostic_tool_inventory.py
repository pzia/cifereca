"""#3913: documented operator commands must remain discoverable on main.

Static contract only: never launch Core, plugins or a networked CLI here.
"""
from __future__ import annotations

import ast
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/cifereca/cli.py"
DOC = ROOT / "docs/CORE-P1-3913-DIAGNOSTIC-TOOLS.md"
BACKLOG = ROOT / ".agents/roles/backlog.md"


def _strings_from_parser_calls() -> tuple[set[str], set[str]]:
    tree = ast.parse(CLI.read_text(encoding="utf-8"))
    commands: set[str] = set()
    options: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"add_argument", "add_parser"}:
            continue
        literals = [arg.value for arg in node.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        if node.func.attr == "add_parser" and literals:
            commands.add(literals[0])
        if node.func.attr == "add_argument":
            options.update(value for value in literals if value.startswith("--"))
    # The generic sync subcommands are registered by a literal loop:
    # for command in ("list", "run"): generic_sub.add_parser(command).
    # Resolve such static iterable registrations without executing the CLI.
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Name):
            continue
        if not isinstance(loop.iter, (ast.Tuple, ast.List)):
            continue
        for call in ast.walk(loop):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "add_parser" and call.args
                    and isinstance(call.args[0], ast.Name)
                    and call.args[0].id == loop.target.id):
                commands.update(item.value for item in loop.iter.elts
                                if isinstance(item, ast.Constant)
                                and isinstance(item.value, str))
                break
    return commands, options


def test_public_cli_entrypoints_and_operator_commands_are_documented():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entrypoints = config["project"]["scripts"]
    assert entrypoints == {
        "cifereca": "cifereca.__main__:main",
        "cifereca-cli": "cifereca.cli:main",
        "cifereca-update": "cifereca.updater:main",
        "cifereca-update-dev": "cifereca.dev_updater:main",
        "cifereca-plugin-test": "cifereca.plugin_test_runner:main",
    }
    document = DOC.read_text(encoding="utf-8")
    for command in entrypoints:
        assert f"`{command}`" in document


def test_operator_diagnostics_and_destructive_commands_have_documented_scope():
    commands, arguments = _strings_from_parser_calls()
    required_commands = {
        "doctor", "sync", "list", "run", "acquisition-audit",
        "statistics", "catalog-list", "acquisition-ledger",
        "acquisition-reconcile", "kobo-audit", "kobo-resume-preview",
        "kobo-resume", "kobo-sync", "kobo-reconcile-local",
        "paris-adept", "audit-origin", "audit-canonical", "audit-transaction",
        "preview-resolution", "bind", "unbind", "plugin", "diagnose",
        "config-get", "config-set", "deps-repair", "identity-directory",
        "catalog-scan",
    }
    required_options = {
        "--provider", "--identity", "--credential", "--account-id",
        "--confirm", "--json", "--report", "--verify-files",
        "--trace-export", "--confirm-trace-export", "--output",
        "--plan", "--refresh-manifest", "--confirm-downloads",
        "--confirm-ownership",
    }
    assert required_commands <= commands
    assert required_options <= arguments

    document = DOC.read_text(encoding="utf-8")
    for name in required_commands:
        assert name in document, name
    for name in required_options:
        assert name in document, name
    assert "cli_milestones_only" in document
    assert "HTTP brut" in document
    assert "Backlog ne lance rien" in document


def test_offline_analyzers_are_real_and_named_in_inventory():
    document = DOC.read_text(encoding="utf-8")
    sources = (
        "src/plugins/provider-numilog/numilog_delivery_diagnose.py",
        "scripts/numilog_html_shape.py",
        "src/plugins/recommendations/trace_report.py",
        "scripts/recette-doctor.py",
        "scripts/benchmark_diagnostics.py",
        "scripts/benchmark_task_admission.py",
    )
    for path in sources:
        assert (ROOT / path).is_file(), path
        assert path in document, path
    assert "pas de placement final" in document
    assert "kobo-resume-preview --credential C --refresh-manifest" in document
    assert "lance rien" in BACKLOG.read_text(encoding="utf-8") or (
        "Le rôle backlog **ne lance pas**" in BACKLOG.read_text(encoding="utf-8")
    )


def test_expert_offline_reconciliation_is_not_misidentified_as_mutating_sync():
    source = (ROOT / "src/cifereca/core/library_reconciliation.py").read_text(
        encoding="utf-8"
    )
    wal = (ROOT / "src/cifereca/core/library_wal_image.py").read_text(
        encoding="utf-8"
    )
    document = DOC.read_text(encoding="utf-8")
    for argument in ("--database", "--root", "--private-report"):
        assert f"parser.add_argument('{argument}'" in source
        assert argument in document
    assert "def main(argv=None):" in source
    assert "acquisition_authorized': False" in source
    assert "os.O_EXCL" in source and "0o600" in source
    assert "library_wal_image.py" in document
    assert "enfant" in document
    assert "def _main():" in wal
    assert "python3 -m cifereca.core.library_reconciliation" in document
