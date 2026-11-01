#!/usr/bin/env python3
"""BnF #2055: local-only DOM signal locator, without exporting private HTML.

Usage (no network, no file write):
  python3 scripts/bnf_borrow_dom_probe.py --page profile < PRIVATE_PROFILE.html
  python3 scripts/bnf_borrow_dom_probe.py --page detail < PRIVATE_BOOK.html

stdout contains a fixed, limited JSON shape and tag names only, NEVER the
source HTML, personal names, account/borrower IDs, counts, CSRF or URLs.
Its candidate labels do NOT constitute borrower eligibility or permission.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/plugins/provider-bnf-pret"))
from cifereca_bnf_pret_qualification import (  # noqa: E402
    PAGES, _empty, qualify_bnf_borrow_dom,
)
from cifereca_bnf_pret_web import MAX_HTML_BYTES  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="BnF DOM structural hints only: NO authorization or network"
    )
    parser.add_argument("--page", choices=sorted(PAGES), required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            result = _empty(args.page, "limit")
        else:
            result = qualify_bnf_borrow_dom(
                raw.decode("utf-8", errors="strict"), page=args.page,
            )
    except (UnicodeError, OSError, ValueError, MemoryError):
        result = _empty(args.page, "unrecognized")
    # Single redacted JSON report. Never write source data to stdout/stderr.
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
