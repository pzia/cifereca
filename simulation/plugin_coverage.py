#!/usr/bin/env python3
"""Inventaire honnête (manifests, services, données, tests) des packages plugins.

Une fixture déclarée ne prouve ni couverture E2E ni fonctionnement de la GUI.
Ne charge pas les plugins et ne fait aucune requête réseau.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
MATRIX = ROOT / "plugin_coverage.toml"
CATALOG = ROOT / "services" / "catalog.toml"
PACKAGES = REPO / "src" / "plugins"
SERVICE_NAMES = {"opds", "kobo", "paris", "numilog", "bnf", "ebooksgratuits"}
DATA_NAMES = SERVICE_NAMES | {"local-library", "specialized-seed"}


def build_report(*, matrix_path: Path = MATRIX, packages_dir: Path = PACKAGES,
                 catalog_path: Path = CATALOG) -> dict:
    spec = tomllib.loads(matrix_path.read_text(encoding="utf-8"))
    if spec.get("version") != 1:
        raise ValueError("plugin_coverage.toml doit déclarer version=1")
    declared = spec.get("package", {})
    if not isinstance(declared, dict):
        raise ValueError("La matrice doit déclarer [package.*]")
    catalog = tomllib.loads(catalog_path.read_text(encoding="utf-8"))["service"]
    existing_services = set(catalog)
    if not SERVICE_NAMES.issubset(existing_services):
        raise ValueError("Le catalogue de services a changé ; réviser la matrice")
    packages: list[dict] = []
    seen = set()
    for file in sorted(packages_dir.glob("*/cifereca-plugin.json")):
        name = file.parent.name
        seen.add(name)
        manifest = json.loads(file.read_text(encoding="utf-8"))
        plugins = manifest.get("plugins", [])
        ids = [p["id"] for p in plugins]
        config = declared.get(name, {})
        data = config.get("data", [])
        if not isinstance(data, list) or any(d not in DATA_NAMES for d in data):
            raise ValueError(f"{name}: entrée data invalide")
        served = sorted(name for name, s in catalog.items()
                        if any(pid in s.get("plugins", []) for pid in ids))
        # A declared service is not considered covered unless a matching manifest declares it.
        unknown = sorted(set(data) & SERVICE_NAMES - set(served))
        if unknown:
            raise ValueError(f"{name}: service(s) non relié(s) au manifeste : {unknown}")
        tests = sorted(path.name for path in (file.parent / "tests").glob("test_*.py"))
        packages.append({
            "package": name,
            "manifest_id": manifest["package"]["id"],
            "plugin_ids": ids,
            "ui_routes": [p["id"] for p in plugins if p.get("type") == "ui.page"],
            "services": served,
            "data": data,
            "unit_test_files": len(tests),
            "e2e_gtk_verified": False,
            "status": "partial" if data else "missing",
            "gap": config.get("gap", "Non classé"),
        })
    missing = sorted(seen - set(declared))
    stale = sorted(set(declared) - seen)
    stats = {
        "packages": len(packages),
        "with_service": sum(bool(r["services"]) for r in packages),
        "with_data": sum(bool(r["data"]) for r in packages),
        "without_data": sum(not r["data"] for r in packages),
        "with_unit_tests": sum(r["unit_test_files"] > 0 for r in packages),
        "e2e_gtk_verified": 0,
    }
    return {"version": 1, "note": "Inventaire statique, PAS un test d'exécution.",
            "stats": stats, "unclassified": missing, "stale": stale, "packages": packages}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Échec si package non classé/obsolète")
    parser.add_argument("--json", action="store_true", help="Rapport JSON")
    args = parser.parse_args(argv)
    result = build_report()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for row in result["packages"]:
            print(f"{row['package']:34s} {row['status']:8s} "
                  f"services={','.join(row['services']) or '-':16s} "
                  f"tests={row['unit_test_files']:2d} e2e=non — {row['gap']}")
        print("\nRésumé :", json.dumps(result["stats"], ensure_ascii=False))
        print("Non classés :", result["unclassified"], "obsolètes :", result["stale"])
    return 2 if args.strict and (result["unclassified"] or result["stale"]) else 0


if __name__ == "__main__":
    sys.exit(main())
