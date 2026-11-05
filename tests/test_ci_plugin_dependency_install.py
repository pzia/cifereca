"""Guard optional plugin PDF test dependencies on main (#4577)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/plugin-tests.yml"


def _installer() -> str:
    yaml = WORKFLOW.read_text(encoding="utf-8")
    return yaml.split("      - name: Install test dependencies\n", 1)[1].split(
        "\n      - name: Run plugin tests", 1
    )[0]


def _pymupdf_eligible(targets: str) -> bool:
    # Execute only the actual Bash condition, never any pip/network action.
    step = _installer()
    start = next(
        line.strip() for line in step.splitlines()
        if line.strip().startswith('if [[ -z "$PLUGIN_TARGETS"')
    )
    condition = start.removeprefix("if ").removesuffix("; then")
    env = {**os.environ, "PLUGIN_TARGETS": targets}
    completed = subprocess.run(
        ["bash", "-c", f"if {condition}; then echo yes; else echo no; fi"],
        env=env, capture_output=True, check=True, text=True, timeout=5,
    )
    return completed.stdout.strip() == "yes"


def test_only_full_or_optimizer_targets_install_pdf_engine():
    assert _pymupdf_eligible("")  # fail closed / manual full
    assert _pymupdf_eligible("src/plugins/epub_optimizer")
    assert _pymupdf_eligible(
        "src/plugins/baen src/plugins/epub_optimizer src/plugins/home-page"
    )
    assert not _pymupdf_eligible("src/plugins/baen")
    assert not _pymupdf_eligible("src/plugins/literary-criticism")
    assert not _pymupdf_eligible("src/plugins/epub_optimizer-fake")


def test_every_other_plugin_gate_and_dependency_remains():
    source = WORKFLOW.read_text(encoding="utf-8")
    step = _installer()
    assert "if: steps.scope.outputs.plugin_mode != 'skip'" in step
    assert 'PLUGIN_TARGETS: ${{ steps.scope.outputs.plugin_targets }}' in step
    assert 'python -m pip install --disable-pip-version-check "PyMuPDF>=1.26,<2"' in step
    assert step.count('"PyMuPDF>=1.26,<2"') == 1
    for item in ('"pytest>=8,<9"', '"httpx>=0.28.1,<0.29"',
                 '"packaging>=24,<26"', '"Pillow>=10,<12"'):
        assert item in step
    assert 'python3 scripts/ci_select_plugin_tests.py' in source
    assert 'bash scripts/ci-run-plugin-tests.sh' in source
    assert '  workflow_dispatch:\n' in source
    assert "    branches: [main]\n" in source
