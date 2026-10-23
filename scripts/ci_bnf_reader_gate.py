"""Select and enforce the BnF reader's real synthetic Chromium acceptance gate."""
from __future__ import annotations

import argparse
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "src/plugins/provider-bnf-pret/"


def selected(paths, root=ROOT, *, full=False):
    # Allows this harness to land before the provider transport PR. An installed
    # transport must never lose its browser acceptance file unnoticed.
    if not (root / PACKAGE / "cifereca_bnf_pret_browser.py").is_file():
        return False
    relevant = full or any(
        (path.startswith(PACKAGE) and path.endswith((".py", ".js")))
        or path in {"scripts/ci_bnf_reader_gate.py", "tests/test_ci_bnf_reader_gate.py",
                    ".github/workflows/pr-ci.yml", "tests/test_bnf_pret_browser_session.py",
                    "tests/test_bnf_pret_web_dom.py"}
        for path in paths
    )
    if relevant and not (root / PACKAGE / "tests/test_browser_chromium.py").is_file():
        raise ValueError("bnf_chromium_acceptance_missing")
    return relevant


def successful(result):
    # unittest returns success for zero tests or an entirely skipped suite.
    # Neither supplies the browser proof required for this gate.
    return result.wasSuccessful() and result.testsRun >= 2 and not result.skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("select", "run"))
    parser.add_argument("changed_file", nargs="?", type=Path)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if args.mode == "select":
        paths = args.changed_file.read_text().splitlines() if args.changed_file else []
        print("true" if selected(paths, full=args.full) else "false")
        return 0
    directory = ROOT / PACKAGE / "tests"
    suite = unittest.TestLoader().discover(str(directory), pattern="test_browser_chromium.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not successful(result):
        print("bnf_chromium_proof_missing_or_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
