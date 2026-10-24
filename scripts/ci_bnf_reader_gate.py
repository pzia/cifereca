"""Select BnF HTTPX contracts without provisioning a browser."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'src/plugins/provider-bnf-pret/'
TEST = PACKAGE + 'tests/test_http_provider.py'
EXPLICIT_TESTS = (PACKAGE + 'tests/test_explicit_token_success.py',
                  PACKAGE + 'tests/test_explicit_token_failures.py',
                  PACKAGE + 'tests/test_observed_loan_metadata.py')


def selected(paths, root=ROOT, *, full=False):
    # This CI tranche can precede the HTTP provider implementation.
    if not (root / PACKAGE / 'cifereca_bnf_pret_http.py').is_file():
        return False
    relevant = full or any(
        path.startswith(PACKAGE)
        or path in {'scripts/ci_bnf_reader_gate.py', 'tests/test_ci_bnf_reader_gate.py',
                    '.github/workflows/pr-ci.yml', 'tests/test_bnf_pret_web_dom.py'}
        for path in paths
    )
    if relevant and not (root / TEST).is_file():
        raise ValueError('bnf_http_acceptance_missing')
    return relevant


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('select', 'run'))
    parser.add_argument('changed_file', nargs='?', type=Path)
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    if args.mode == 'select':
        paths = args.changed_file.read_text().splitlines() if args.changed_file else []
        print('true' if selected(paths, full=args.full) else 'false')
        return 0
    # pytest discovers the Core/SQLite and official-package integration functions,
    # as well as the unittest HTTPX contracts. A missing/empty suite fails pytest.
    return subprocess.call([sys.executable, '-m', 'pytest', '-q', TEST, *EXPLICIT_TESTS], cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
