"""#3913: inspect direct plugin CLIs from a source checkout, without importing code.

This module does not import provider modules, load manifests, start Cifereca
Core, access SQLite or make HTTP requests. It reads source text and AST only.
It is intentionally a source-checkout tool, not an installed provider runtime.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


# Classification is deliberately explicit: a newly detected direct executable
# is marked "unclassified" until an owner reviews its actual side effects.
CLASSIFIED: dict[str, tuple[str, str]] = {
    "src/plugins/provider-numilog/numilog_delivery_diagnose.py": (
        "F", "lecture hors réseau d'un ZIP journal existant",
    ),
    "src/plugins/recommendations/trace_report.py": (
        "F", "analyse locale de traces de performance",
    ),
    "src/plugins/epub_optimizer/cifereca_epub_optimizer/cli.py": (
        "M", "transformation d'un EPUB et écriture d'un fichier de sortie",
    ),
    "src/plugins/epub_optimizer/tests/benchmark_optimizer_cpu.py": (
        "T/M", "benchmark synthétique créant des fichiers et baselines",
    ),
}


def _has_cli_entrypoint(tree: ast.AST) -> bool:
    """Check syntax only; string literals containing __main__ do not count."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            condition = node.test
            if (isinstance(condition.left, ast.Name)
                    and condition.left.id == "__name__"
                    and len(condition.ops) == 1
                    and isinstance(condition.ops[0], ast.Eq)
                    and len(condition.comparators) == 1
                    and isinstance(condition.comparators[0], ast.Constant)
                    and condition.comparators[0].value == "__main__"):
                return True
        if isinstance(node, ast.Call):
            function = node.func
            if ((isinstance(function, ast.Attribute)
                 and isinstance(function.value, ast.Name)
                 and function.value.id == "argparse"
                 and function.attr == "ArgumentParser")
                    or (isinstance(function, ast.Name)
                        and function.id == "ArgumentParser")):
                return True
    return False


def discover_plugin_entrypoints(root: Path) -> tuple[str, ...]:
    """Return sorted source-relative direct CLIs; never execute their code."""
    folder = root / "src" / "plugins"
    if not folder.is_dir():
        raise ValueError("plugin_source_root_unavailable")
    found: set[str] = set()
    for path in folder.rglob("*.py"):
        if path.name.startswith("test_"):
            continue  # pytest fixtures aren't operator commands
        try:
            syntax = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ValueError("plugin_entrypoint_source_unreadable") from exc
        if _has_cli_entrypoint(syntax):
            found.add(path.relative_to(root).as_posix())
    return tuple(sorted(found))


def inventory(root: Path) -> dict[str, object]:
    found = discover_plugin_entrypoints(root)
    entries = [
        {
            "path": path,
            "risk": CLASSIFIED[path][0] if path in CLASSIFIED else "unclassified",
            "reason": CLASSIFIED[path][1] if path in CLASSIFIED else "review_required",
        }
        for path in found
    ]
    unclassified = [row["path"] for row in entries if row["risk"] == "unclassified"]
    # If a previously audited script disappeared, force an explicit review:
    # the historical documentation may now be unsafe or misleading.
    removed = sorted(set(CLASSIFIED).difference(found))
    return {
        "status": "needs_review" if unclassified or removed else "complete",
        "entries": entries,
        "unclassified": unclassified,
        "removed": removed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inventaire statique des points d'entrée directs des plugins."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="racine d'un checkout source de Cifereca")
    parser.add_argument("--json", action="store_true",
                        help="rapport des chemins relatifs et effets classifiés")
    args = parser.parse_args(argv)
    try:
        result = inventory(args.root)
    except ValueError:
        result = {"status": "unavailable", "entries": [],
                  "unclassified": [], "removed": []}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
