"""#3196: count provider-specific Kobo Python surface still shipped by Core.

Dedicated-file LOC is exact for the explicitly documented scope below.
Incidental "kobo" textual hits in other Core files are a separate lower
bound, NOT an exact count of all Kobo-specific blocks in generic modules.
No imports of cifereca, plugin discovery, filesystem writes or network I/O.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _category(relative: Path) -> str | None:
    parts = relative.parts
    name = relative.name.casefold()
    if parts[:3] == ("src", "cifereca", "core") and "kobo" in name:
        return "core"
    if parts[:3] == ("src", "cifereca", "kobo"):
        return "kobo_subpackage"
    if (parts[:3] == ("src", "cifereca", "gui")
            and "legacy" in name and "kobo" in name):
        return "gui_legacy"
    return None


def inventory(root: Path) -> dict[str, object]:
    """Count physical lines, not syntactic statements; no exclusions by status."""
    root = root.resolve()
    source = root / "src" / "cifereca"
    groups = {"core": 0, "kobo_subpackage": 0, "gui_legacy": 0}
    dedicated: dict[str, int] = {}
    active = 0
    outside_mentions = 0
    outside_files = 0
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(root)
        category = _category(relative)
        lines = path.read_text(encoding="utf-8").splitlines()
        if category is not None:
            groups[category] += len(lines)
            dedicated[relative.as_posix()] = len(lines)
            active += sum(
                bool(line.strip()) and not line.lstrip().startswith("#")
                for line in lines
            )
        else:
            mentions = sum("kobo" in line.casefold() for line in lines)
            outside_mentions += mentions
            outside_files += bool(mentions)
    return {
        "metric": "core.kobo.dedicated-python-lines/1",
        "groups": groups,
        "dedicated_files": len(dedicated),
        "dedicated_lines": sum(groups.values()),
        "nonblank_noncomment_lines": active,
        "outside_dedicated_kobo_literal_lines": outside_mentions,
        "outside_dedicated_files_with_kobo_literal": outside_files,
        "per_file": dedicated,
        "scope": (
            "src/cifereca/core/**/*kobo*.py; src/cifereca/kobo/**/*.py; "
            "src/cifereca/gui/*legacy*kobo*.py"
        ),
        "limitation": (
            "Generic-module Kobo-specific blocks without the literal 'kobo' "
            "are not measured; external plugins and non-Python files excluded."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = inventory(args.root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(
            f"Kobo Core dedicated: {result['dedicated_lines']} lines / "
            f"{result['dedicated_files']} files "
            f"({result['nonblank_noncomment_lines']} nonblank/noncomment); "
            f"generic-file literal mentions: "
            f"{result['outside_dedicated_kobo_literal_lines']} lines"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
