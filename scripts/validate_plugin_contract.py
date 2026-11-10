"""Shared per-package checks replacing repeated test_contract_038.py modules.

Run by ci-run-plugin-tests.sh before pytest, including targeted runs.
Only packages migrated from the historical contract suite are checked here.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

PLUGIN_API_RANGES = {
    "baen": ">=1.4.1,<2",
    "home-page": ">=1.3,<2",
    "provider-kobo": ">=1.4.3,<2",
    "epub_optimizer": ">=1.4,<2",
    "provider-paris": ">=1.4.6,<2",
    "ebooksgratuits": ">=1.4.1,<2",
    "provider-gallica": ">=1.4.3,<2",
    "provider-numilog": ">=1.4.1,<2",
    "runtime-kobo-adept": ">=1.4,<2",
    "provider-gutenberg": ">=1.4.1,<2",
    "provider-bnf-catalogue": ">=1.4.1,<2",
}
FIELDS = ("requires", "recommends", "capabilities", "requires_brokers",
          "requires_contracts", "system_requires")


def validate(root: Path) -> list[str]:
    """Return contract violations, maintaining the former package checks."""
    if root.name not in PLUGIN_API_RANGES:
        return []
    failures = []
    try:
        data = json.loads((root / "cifereca-plugin.json").read_text(encoding="utf-8"))
        package = data["package"]
        plugins = data["plugins"]
        if data.get("manifest") != 1:
            failures.append("manifest must equal 1")
        if package.get("plugin_api") != PLUGIN_API_RANGES[root.name]:
            failures.append("plugin_api range changed unexpectedly")
        if not isinstance(plugins, list) or not plugins:
            failures.append("plugins must be a nonempty list")
        else:
            for plugin in plugins:
                name = plugin.get("id", "<unknown>")
                if plugin.get("version") != package["version"]:
                    failures.append(f"{name}: version must equal package version")
                for field in FIELDS:
                    if not isinstance(plugin.get(field), list):
                        failures.append(f"{name}: {field} missing or not a list")
                if not str(plugin.get("help_text") or "").strip():
                    failures.append(f"{name}: missing help_text")
                if "requires_services" in plugin:
                    failures.append(f"{name}: retired requires_services")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        failures.append(f"invalid manifest: {exc}")

    for path in sorted(root.rglob("*.py")):
        if "tests" in path.relative_to(root).parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            failures.append(f"{path.relative_to(root)}: unparseable source: {exc}")
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("cifereca.") and name != "cifereca.plugin_api":
                    failures.append(f"{path.relative_to(root)}:{node.lineno}: private import {name}")
    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: validate_plugin_contract.py PACKAGE_DIR", file=sys.stderr)
        return 2
    root = Path(argv[0]).resolve()
    failures = validate(root)
    for failure in failures:
        print(f"{root.name}: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
