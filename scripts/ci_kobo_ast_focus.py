"""P0 #5048/#3196: deterministic, read-only Kobo AST worklist on installed Core.

Signals are candidates for human review, not proof of active authority.
No plugin import, runtime execution, account access, network or writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_provider_core_coupling_inventory import (
    _dedicated_provider, inspect_source,
)
from scripts.ci_provider_core_coupling_report import (
    _installed_provider_aliases, _SIGNAL_KINDS,
)


def inventory(root: Path) -> dict[str, object]:
    root = root.resolve()
    source = root / "src" / "cifereca"
    aliases = _installed_provider_aliases(root)
    kobo_aliases = frozenset(
        alias for alias in aliases
        if alias == "kobo" or alias.startswith("provider.kobo")
    )
    grouped: dict[str, dict[int, set[str]]] = {}
    parse_errors: dict[str, str] = {}
    if source.is_dir():
        for path in sorted(source.rglob("*.py")):
            relative = path.relative_to(root)
            # A dedicated file is counted once in the physical LOC inventory.
            if _dedicated_provider(relative) is not None:
                continue
            rel = relative.as_posix()
            try:
                findings = inspect_source(
                    path.read_text(encoding="utf-8"), providers=aliases,
                )
            except (SyntaxError, UnicodeError) as exc:
                parse_errors[rel] = type(exc).__name__
                continue
            by_line: dict[int, set[str]] = {}
            for kind in _SIGNAL_KINDS:
                for hit in findings[kind]:
                    if kobo_aliases.intersection(hit["providers"]):
                        by_line.setdefault(int(hit["line"]), set()).add(kind)
            if by_line:
                grouped[rel] = by_line

    rows = [
        {"path": path, "line": line, "kinds": sorted(kinds)}
        for path, lines in sorted(grouped.items())
        for line, kinds in sorted(lines.items())
    ]
    kinds = Counter(kind for row in rows for kind in row["kinds"])
    by_file = [
        {"path": path, "unique_signal_lines": len(lines),
         "kinds": dict(sorted(Counter(
             kind for found in lines.values() for kind in found
         ).items()))}
        for path, lines in grouped.items()
    ]
    by_file.sort(key=lambda row: (-row["unique_signal_lines"], row["path"]))
    return {
        "metric": "core.kobo.ast-worklist/1",
        "unique_signal_lines": len(rows),
        "signal_occurrences_by_kind": dict(sorted(kinds.items())),
        "files_with_signals": len(grouped),
        "per_file": by_file,
        "findings": rows,
        "parse_errors": parse_errors,
        "scope": "src/cifereca/**/*.py excluding dedicated provider files",
        "limitation": (
            "Static AST signals are not proven executable Kobo policy; "
            "one line may carry multiple kinds. False negatives and indirect "
            "coupling are possible. Plugin code and dedicated LOC are excluded."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inventory(args.root)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Kobo generic AST: {report['unique_signal_lines']} unique lines "
              f"in {report['files_with_signals']} files")
        for row in report["per_file"]:
            print(f"  {row['unique_signal_lines']:3} {row['path']}")
        if report["parse_errors"]:
            print(f"Parse errors: {len(report['parse_errors'])}")
    return int(bool(report["parse_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
