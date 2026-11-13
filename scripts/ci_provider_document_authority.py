#!/usr/bin/env python3
"""Bounded, offline provider-document authority gate (#5519).

Only priority navigation and byte-identical Browser Recorder source copies are
normative checks. Historic ticket notes are intentionally not linted by keywords.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs/PROVIDER-DOCUMENT-AUTHORITY.md"
SOURCE = ROOT / "docs/PROVIDER-DEVELOPMENT.md"
COPY = ROOT / "src/plugins/browser-recorder/PROVIDER-DEVELOPMENT.md"

ENTRYPOINTS = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "src/plugins/README.md",
    ROOT / ".agents/roles/README.md",
    ROOT / ".agents/roles/plugins.md",
    ROOT / ".agents/roles/core.md",
    ROOT / ".agents/roles/focus.md",
    ROOT / ".agents/roles/cicd.md",
    ROOT / ".agents/roles/ux.md",
    ROOT / ".agents/roles/backlog.md",
    ROOT / "docs/PLUGIN-BOUNDARIES.md",
)

# These are actual installed sources, not examples of a proposed future ABI.
AUTHORITIES = (
    ROOT / "docs/ARCHITECTURE.md",
    SOURCE,
    ROOT / "docs/PLUGIN-BOUNDARIES.md",
    ROOT / "src/cifereca/plugin_api/__init__.py",
    ROOT / "src/cifereca/providers/base.py",
    ROOT / "src/plugins/RIGHTS-OBSERVATION-CONFORMANCE.md",
    ROOT / "src/plugins/provider-bnf-pret/QUALIFICATION.md",
)

# Check only the links actually important to authority/navigation, not all
# historical README/release links or all ~343 archival documents.
INDEX_LINKS = (
    "ARCHITECTURE.md",
    "PROVIDER-DEVELOPMENT.md",
    "PLUGIN-BOUNDARIES.md",
    "../src/cifereca/plugin_api/",
    "../src/cifereca/providers/base.py",
    "../src/plugins/browser-recorder/PROVIDER-DEVELOPMENT.md",
    "../src/plugins/browser-recorder/browser_recorder_devkit.py",
    "../src/plugins/RIGHTS-OBSERVATION-CONFORMANCE.md",
    "../src/plugins/provider-bnf-pret/QUALIFICATION.md",
    "LOGGING-CONTRACT.md",
    "DIAGNOSTICS_CANONICAL_JOURNAL.md",
)

def check(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    index = root / INDEX.relative_to(ROOT)
    source = root / SOURCE.relative_to(ROOT)
    copy = root / COPY.relative_to(ROOT)
    if not index.is_file():
        return ["Missing canonical provider document authority index"]
    index_text = index.read_text(encoding="utf-8")
    for relative in INDEX_LINKS:
        if f"]({relative})" not in index_text:
            errors.append(f"Missing normative index link: {relative}")
        if not (index.parent / relative).exists():
            errors.append(f"Broken normative index target: {relative}")
    for path in AUTHORITIES:
        candidate = root / path.relative_to(ROOT)
        if not candidate.exists():
            errors.append(f"Missing installed authority: {candidate.relative_to(root)}")
    for path in ENTRYPOINTS:
        candidate = root / path.relative_to(ROOT)
        if not candidate.is_file():
            errors.append(f"Missing entrypoint: {candidate.relative_to(root)}")
            continue
        text = candidate.read_text(encoding="utf-8")
        prefix = "\n".join(text.splitlines()[:20])
        reference = (
            Path("PROVIDER-DOCUMENT-AUTHORITY.md")
            if candidate.parent == index.parent
            else Path("../" * len(candidate.parent.relative_to(root).parts))
                 / "docs/PROVIDER-DOCUMENT-AUTHORITY.md"
        )
        if reference.as_posix() not in prefix:
            errors.append(f"Entrypoint does not prioritize index: {candidate.relative_to(root)}")
    if not source.is_file() or not copy.is_file():
        errors.append("Missing canonical or Browser Recorder provider guide")
    elif source.read_bytes() != copy.read_bytes():
        errors.append("Browser Recorder provider guide differs byte-for-byte from canonical guide")
    if "cifereca.plugin_api" not in index_text:
        errors.append("Index must identify installed public API as implementation authority")
    return errors


def main() -> int:
    errors = check()
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    if errors:
        return 1
    print("PASS: canonical authority index, entrypoints, and byte-identical dev-kit guide")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
