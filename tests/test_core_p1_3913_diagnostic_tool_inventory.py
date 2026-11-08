"""#3913: documented operator commands must remain discoverable on main.

Static contract only: never launch Core, plugins or a networked CLI here.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
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
        "--refresh-manifest", "--confirm-downloads",
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


def test_private_gui_capture_zip_has_documented_offline_integrity_validator():
    source = (ROOT / "src/cifereca/core/capture_session_export.py").read_text(
        encoding="utf-8"
    )
    operator = DOC.read_text(encoding="utf-8")
    capture = (ROOT / "docs/CORE-P1-4093-GUI-CAPTURE.md").read_text(
        encoding="utf-8"
    )
    command = "python3 -m cifereca.core.capture_session_export --validate"
    assert command in operator and command in capture
    assert "def main(argv: list[str] | None = None) -> int:" in source
    assert "validate_capture_session(args.validate)" in source
    assert "sans démarrage Core" in operator
    assert "aucun chemin" in operator
    assert "N+M" in operator


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


def test_plugin_direct_executables_are_explicitly_classified_without_running_them():
    """New direct plugin CLIs require an owner-reviewed effect classification."""
    from cifereca.core.plugin_entrypoint_inventory import CLASSIFIED, inventory

    document = DOC.read_text(encoding="utf-8")
    report = inventory(ROOT)
    assert report["status"] == "complete", report
    assert report["unclassified"] == []
    assert report["removed"] == []
    assert len(report["entries"]) == 5
    assert {row["path"] for row in report["entries"]} == set(CLASSIFIED)
    assert {row["risk"] for row in report["entries"]} == {"F", "M", "T/M"}
    for row in report["entries"]:
        assert f'`{row["path"]}`' in document, row["path"]
        assert f"**{row['risk']}**" in document, row["risk"]
    for statement in (
        "Points d'entrée directs dans les packages plugins",
        "Benchmark destructeur", "**F**", "**T/M**",
    ):
        assert statement.lower() in document.lower(), statement


def test_plugin_entrypoint_inventory_flags_unknown_command_and_removed_classifier(
    tmp_path,
):
    from cifereca.core.plugin_entrypoint_inventory import inventory

    (tmp_path / "src/plugins/unknown").mkdir(parents=True)
    fake_cli = tmp_path / "src/plugins/unknown/new_cli.py"
    fake_cli.write_text(
        'import argparse\nif __name__ == "__main__":\n'
        '    argparse.ArgumentParser()\n',
        encoding="utf-8",
    )
    report = inventory(tmp_path)
    assert report["status"] == "needs_review"
    assert report["unclassified"] == ["src/plugins/unknown/new_cli.py"]
    assert report["removed"]
    assert report["entries"][0]["risk"] == "unclassified"
    # AST discovery is inert: source code isn't imported or executed.
    fake_cli.write_text(
        'raise RuntimeError("NO_EXECUTION_ALLOWED")\n'
        'if __name__ == "__main__": pass\n',
        encoding="utf-8",
    )
    report = inventory(tmp_path)
    assert report["status"] == "needs_review"


def test_plugin_entrypoint_inspector_cli_is_offline_and_has_two_output_modes(
    tmp_path,
):
    """Run only the safe inspector; never invoke an executable plugin."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + (
        environment.get("PYTHONPATH") or ""
    )

    def run(*options):
        return subprocess.run(
            [
                sys.executable, "-m",
                "cifereca.core.plugin_entrypoint_inventory",
                "--root", str(ROOT), *options,
            ],
            cwd=tmp_path, env=environment,
            text=True, capture_output=True, timeout=15, check=False,
        )

    structured = run("--json")
    assert structured.returncode == 0
    assert structured.stderr == ""
    payload = json.loads(structured.stdout)
    assert payload["status"] == "complete"
    assert len(payload["entries"]) == 5
    assert payload["unclassified"] == []
    assert payload["removed"] == []
    assert all(not entry["path"].startswith("/") for entry in payload["entries"])

    human = run()
    assert human.returncode == 0
    assert human.stderr == ""
    assert human.stdout == (
        "plugin_entrypoints_complete count=5 unclassified=0 removed=0\\n"
    ).replace("\\n", "\n")
    assert str(ROOT) not in human.stdout

    absent = subprocess.run(
        [
            sys.executable, "-m",
            "cifereca.core.plugin_entrypoint_inventory",
            "--root", str(tmp_path), "--json",
        ],
        cwd=tmp_path, env=environment,
        text=True, capture_output=True, timeout=15, check=False,
    )
    assert absent.returncode == 2
    assert json.loads(absent.stdout)["status"] == "unavailable"
    assert absent.stderr == ""


def test_direct_scripts_with_main_or_argparse_are_present_in_inventory():
    """Fail if a new direct scripts/ CLI bypasses the #3913 risk classification."""
    document = DOC.read_text(encoding="utf-8")
    candidates = 0
    for path in sorted((ROOT / "scripts").glob("*.py")):
        # A pytest test module is not an operator CLI even if it includes
        # examples of ArgumentParser or an __main__ guard in a fixture.
        if path.name.startswith("test_"):
            continue
        source = path.read_text(encoding="utf-8")
        if "__main__" not in source and "ArgumentParser" not in source:
            continue
        candidates += 1
        assert path.name in document, f"unclassified_scripts_entrypoint:{path.name}"
    assert candidates >= 30


def test_shell_scripts_and_environment_launchers_are_classified():
    """Do not add an executable shell gate that can mutate the host silently."""
    document = DOC.read_text(encoding="utf-8")
    candidates = [
        *sorted((ROOT / "scripts").glob("*.sh")),
        *sorted((ROOT / "environnements").glob("*/*")),
    ]
    found = set()
    for path in candidates:
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            header = stream.read(32)
        if not header.startswith(b"#!/usr/bin/env bash"):
            continue
        relative = path.relative_to(ROOT).as_posix()
        found.add(relative)
        assert relative in document, f"unclassified_shell_entrypoint:{relative}"
    assert len(found) >= 11
    assert "scripts/ci-install-gtk-e2e-runtime.sh" in found
    assert "scripts/ci-plugins-only-runtime-gate.sh" in found


def test_adept_snapshot_diagnostic_is_not_described_as_server_evidence():
    document = DOC.read_text(encoding="utf-8")
    assert "validate-resolution-snapshot" in document
    assert "sans mutation ni attestation Adobe serveur" in document
    assert "ticket P0 #3909" in document
