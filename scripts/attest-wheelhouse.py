#!/usr/bin/env python3
"""Record exact PyPI wheel eligibility for a trusted/signed release wheelhouse.

Runs after wheels have been obtained. Download-time admission and cryptographic
signing of the resulting release artifact are separate gates (#2770).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cifereca.wheelhouse_quarantine import attest_wheelhouse
from cifereca.plugins.python_quarantine import QuarantineRejected, quarantine_diagnostic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attest exact Python wheels for an offline release")
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("--core-version", help="Locally built Cifereca Core version (exempt wheel)")
    args = parser.parse_args(argv)
    try:
        attest_wheelhouse(args.wheelhouse, core_version=args.core_version)
    except (QuarantineRejected, OSError, ValueError) as exc:
        # Never echo trusted index URLs or local filesystem paths in logs.
        code = quarantine_diagnostic(exc) if isinstance(exc, QuarantineRejected) else "dependency_provenance_unavailable"
        print(code, file=sys.stderr)
        return 2
    print("wheelhouse_provenance_verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
