"""P1 #3122: run Paris/BnF public lending conformance on provider changes.

Only current real plugin adapters over synthetic data; no vendor HTTP, ACSM,
ADEPT, credential, browser or update side effects.  Missing tests fail closed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("src/plugins/provider-paris/", "src/plugins/provider-bnf-pret/")
TESTS = (
    "tests/test_core_p1_3122_real_manifest_lending_conformance.py",
    "tests/test_core_p1_3122_paris_bnf_snapshots.py",
)
GATE_FILES = frozenset({
    "scripts/ci_lending_conformance_gate.py",
    "tests/test_ci_lending_conformance_gate.py",
    *TESTS,
    ".github/workflows/pr-ci.yml",
})


def selected(paths, root=ROOT, *, full=False):
    relevant = full or any(
        path.startswith(PACKAGES) or path in GATE_FILES
        for path in paths
    )
    if relevant and any(not (root / path).is_file() for path in TESTS):
        raise ValueError("lending_conformance_suite_missing")
    return relevant


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("select", "run"))
    parser.add_argument("changed_file", nargs="?", type=Path)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if args.mode == "select":
        paths = args.changed_file.read_text(encoding="utf-8").splitlines() if args.changed_file else []
        print("true" if selected(paths, full=args.full) else "false")
        return 0
    selected([], full=True)
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *TESTS], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
