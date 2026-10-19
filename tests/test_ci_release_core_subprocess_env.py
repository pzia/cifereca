"""Release Core regression: import scope of subprocess tests is explicit (#2095)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github/workflows/pr-ci.yml"
RELEASE = ROOT / ".github/workflows/release.yml"
EXPECTED = "PYTHONPATH: ${{ github.workspace }}/src:${{ github.workspace }}/src/plugins/provider-kobo"


def _core_step(path: Path) -> str:
    workflow = path.read_text(encoding="utf-8")
    return workflow.split("      - name: Run Core tests\n", 1)[1].split("\n      - name:", 1)[0]


def test_release_core_tests_inherit_same_import_scope_as_pr_ci():
    assert EXPECTED in _core_step(CI)
    release_step = _core_step(RELEASE)
    assert EXPECTED in release_step
    assert "        run: python -m pytest -q tests\n" in release_step
    assert "      - name: Run plugin tests\n" in RELEASE.read_text(encoding="utf-8")


def test_release_import_scope_reaches_python_subprocess_outside_repository():
    # Python subprocesses do not inherit pytest's sys.path mutation, but they
    # must be able to resolve the uninstalled Core module from a neutral cwd.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((
        str(ROOT / "src"), str(ROOT / "src/plugins/provider-kobo"),
    ))
    with tempfile.TemporaryDirectory() as directory:
        completed = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util; "
             "assert importlib.util.find_spec('cifereca.cli') is not None"],
            cwd=directory, env=env, capture_output=True, text=True,
            timeout=15, check=False,
        )
    assert completed.returncode == 0, completed.stderr


def test_rearm_keeps_authorized_version_and_release_is_push_scoped():
    tag = (ROOT / ".github/release-trigger").read_text(encoding="utf-8")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert "".join(tag.split()) == f"v{version}"
    workflow = RELEASE.read_text(encoding="utf-8")
    assert '      - ".github/release-trigger"' in workflow
    assert "  workflow_dispatch:\n" in workflow
