"""P0 #5069 B4 — read-only aggregate updater maintenance inspection.

Deliberately NOT a garbage collector: neither the inventory nor the policy
planner is proof that all cross-store rollback references have been qualified.
No filesystem writes, no hashes of user content, no plugin execution.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

from . import updater
from .paths import data_dir
from .updater_retention_inventory import inspect_core_transaction_history
from .updater_retention_plugin_inventory import inspect_plugin_version_history


def _summarize(plan: dict[str, Any] | None, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = plan.get("objects", []) if type(plan) is dict else []
    output: list[dict[str, Any]] = []
    for kind in kinds:
        prefix = kind + ":"
        selected = [
            row for row in rows
            if type(row) is dict and type(row.get("object_id")) is str
            and row["object_id"].startswith(prefix)
        ]
        output.append({
            "store": kind,
            "observed": len(selected),
            "retained": sum(row.get("decision") == "retain" for row in selected),
            # A candidate is merely a theoretical policy result, not a
            # deletion instruction; never authorize anything from this count.
            "candidates_unverified": sum(
                row.get("decision") == "candidate" for row in selected
            ),
        })
    return output


def inspect_maintenance(
    *, runtime_root: Path | None = None,
    packages_root: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Cross-store *summary* only; no unsafe cross-store graph merge."""
    runtime_root = runtime_root if runtime_root is not None else updater.install_root()
    # The standard updater._plugin_packages_root() calls data_dir() and
    # creates a directory. Inspection must not do so, even when not installed.
    packages_root = (packages_root if packages_root is not None
                     else data_dir(create=False) / "plugins" / "packages")
    instant = time.time() if now is None else now
    core = inspect_core_transaction_history(runtime_root, now=instant)
    plugins = inspect_plugin_version_history(packages_root, now=instant)
    blockers = {
        *(f"core:{reason}" for reason in core["blockers"]),
        *(f"plugins:{reason}" for reason in plugins["blockers"]),
        "cross_store_reference_graph_not_attested",
        "updater_backup_ownership_unqualified",
        "persistent_staging_ownership_unqualified",
        "plugin_activation_lock_not_shared",
    }
    return {
        "schema": "cifereca.updater.maintenance-inspect/1",
        "ok": True, "read_only": True, "safe_to_apply": False,
        "coverage": sorted(set(core.get("coverage", []) + plugins.get("coverage", []))),
        "pending_stores": [
            "updater_backups", "persistent_staging", "cross_store_references",
        ],
        "stores": _summarize(
            core.get("plan"),
            ("release", "transaction", "snapshot"),
        ) + _summarize(plugins.get("plan"), ("version",)),
        "blockers": sorted(blockers),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic de maintenance de l'Updater, lecture seule uniquement",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--apply", action="store_true",
        help="Réservé : suppression systématiquement refusée dans cette version",
    )
    args = parser.parse_args(argv)
    if args.apply:
        result = {
            "ok": False, "safe_to_apply": False,
            "error": "maintenance_apply_not_available",
        }
        print(json.dumps(result, sort_keys=True) if args.json
              else "maintenance_apply_not_available")
        return 2
    try:
        result = inspect_maintenance()
    except Exception:
        # Errors can originate in untrusted filesystem metadata. Never echo
        # arbitrary paths, identifiers, exception text, or traceback.
        result = {
            "ok": False, "read_only": True, "safe_to_apply": False,
            "error": "maintenance_inspect_unavailable",
        }
        print(json.dumps(result, sort_keys=True) if args.json
              else "maintenance_inspect_unavailable")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print("Updater maintenance: READ ONLY; apply unavailable")
        for item in result["stores"]:
            print(
                f"{item['store']}: {item['observed']} observed, "
                f"{item['retained']} retained, "
                f"{item['candidates_unverified']} theoretical candidates"
            )
        print(f"blockers: {', '.join(result['blockers'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
