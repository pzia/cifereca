"""P0 #5048: reproducible physical-line inventory of provider-specific Core code.

This is a *surface* metric, never proof that a provider dispatch is reachable.
It does not count plugin implementations or test fixtures. Generic modules
are reported separately: provider-named lines are not necessarily executable
coupling and cannot be added to dedicated LOC without careful review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


# Keep Kobo scope identical to ci_kobo_core_line_inventory.py (#3196).
_PROVIDER_NAMES = ("kobo", "numilog", "bnf", "biblioaccess")
_GENERIC_ROOTS = {"services.py", "brokers.py", "storage.py"}
_SOURCE = Path("src") / "cifereca"


def _category(path: Path) -> str | None:
    """Return the provider for a dedicated implementation filename."""
    parts = path.parts
    if len(parts) < 3 or parts[:2] != ("src", "cifereca"):
        return None
    name = path.name.casefold()
    if parts[2] == "kobo":
        return "kobo"
    if parts[2] == "core":
        for provider in _PROVIDER_NAMES:
            if provider in name:
                return provider
    if parts[2] == "gui" and "legacy" in name and "kobo" in name:
        return "kobo"
    return None


def inventory(root: Path) -> dict[str, object]:
    """Count physical Python lines without importing or executing Core."""
    root = root.resolve()
    dedicated: dict[str, dict[str, object]] = {}
    generic: dict[str, dict[str, object]] = {}
    dedicated_totals = {provider: 0 for provider in _PROVIDER_NAMES}
    source = root / _SOURCE
    if source.exists():
        for file in sorted(source.rglob("*.py")):
            relative = file.relative_to(root)
            name = relative.as_posix()
            lines = file.read_text(encoding="utf-8").splitlines()
            provider = _category(relative)
            if provider is not None:
                dedicated[name] = {
                    "provider": provider,
                    "lines": len(lines),
                    "nonblank_noncomment": sum(
                        bool(line.strip()) and not line.lstrip().startswith("#")
                        for line in lines
                    ),
                }
                dedicated_totals[provider] += len(lines)
            else:
                mentions = {
                    provider: sum(provider in line.casefold() for line in lines)
                    for provider in _PROVIDER_NAMES
                }
                if any(mentions.values()):
                    generic[name] = {
                        "lines": len(lines),
                        "provider_literal_lines": mentions,
                        "mixed_provider_lines": sum(
                            any(provider in line.casefold() for provider in _PROVIDER_NAMES)
                            for line in lines
                        ),
                    }
    return {
        "metric": "core.provider.dedicated-python-lines/1",
        "scope": "src/cifereca/**/*.py excluding src/plugins, tests and scripts",
        "dedicated_lines": sum(dedicated_totals.values()),
        "dedicated_files": len(dedicated),
        "by_provider": dedicated_totals,
        "per_file": dedicated,
        "generic_files_with_provider_literal": len(generic),
        "generic_provider_literal_lines_unique": sum(
            item["mixed_provider_lines"] for item in generic.values()
        ),
        "generic_per_file": generic,
        "limitations": [
            "Dedicated filenames can contain generic guards retained by Core; no automatic deletion.",
            "Generic literal lines are not a complete count of provider-specific logic.",
            "Names used as persisted data or diagnostics are not necessarily executable coupling.",
            "This report does not prove reachability; cross-check the AST provider coupling gate.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inventory(args.root)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"Provider dedicated Core: {report['dedicated_lines']} lines / "
            f"{report['dedicated_files']} files; "
            f"generic literal lines (unique): "
            f"{report['generic_provider_literal_lines_unique']}"
        )
        for provider, count in report["by_provider"].items():
            print(f"  {provider}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
