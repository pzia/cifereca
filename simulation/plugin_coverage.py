#!/usr/bin/env python3
"""Deterministic, non-executing inventory of plugin simulation coverage (#770).

Evidence paths indicate that a scenario exists, NEVER that it passed. In
particular, an E2E script does not establish successful GTK execution.
No provider is imported, no credentials are read, and no network is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import sys
import tomllib

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
MATRIX = ROOT / "plugin_coverage.toml"
CATALOG = ROOT / "services" / "catalog.toml"
PACKAGES = REPO / "src" / "plugins"
PROFILES = ROOT / "profiles"

# Historical fields retained for consumers of the first report.
SERVICE_NAMES = {"opds", "kobo", "paris", "numilog", "bnf", "ebooksgratuits"}
DATA_NAMES = SERVICE_NAMES | {"local-library", "specialized-seed"}
EVIDENCE_FIELDS = ("contract_tests", "failure_tests", "gtk_scenarios")
STATES = {"covered", "partial", "absent", "not_applicable"}


def _evidence_paths(repo: Path, package: str, config: dict, field: str) -> list[str]:
    """Resolve declared static evidence against the repository, never blindly."""
    names = config.get(field, [])
    if not isinstance(names, list) or any(type(name) is not str for name in names):
        raise ValueError(f"{package}: {field} doit être une liste de chemins")
    if len(names) != len(set(names)):
        raise ValueError(f"{package}: {field} contient des doublons")
    checked = []
    root = repo.resolve()
    for name in names:
        path = PurePosixPath(name)
        parts = path.parts
        if (not parts or path.is_absolute() or ".." in parts or "." in parts
                or name != path.as_posix() or "\\" in name or not name.endswith(".py")):
            raise ValueError(f"{package}: {field} contient un chemin invalide")
        allowed = (
            parts[:2] == ("simulation", "tests")
            or parts[0] == "tests"
            or parts[:4] == ("src", "plugins", package, "tests")
            or (field == "gtk_scenarios" and parts[0] == "e2e")
        )
        if not allowed:
            raise ValueError(f"{package}: {field} hors des répertoires de recette")
        # GTK scripts may be e2e probes; normal evidence must be test_*.py.
        if field != "gtk_scenarios" and not path.name.startswith("test_"):
            raise ValueError(f"{package}: {field} ne référence pas un test")
        target = repo / name
        try:
            target.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{package}: {field} référence un fichier absent ou extérieur") from exc
        if not target.is_file():
            raise ValueError(f"{package}: {field} ne référence pas un fichier")
        checked.append(name)
    return checked


def _profile_fixtures(profiles_dir: Path) -> list[tuple[str, set[str]]]:
    """Only fixture availability; a service is not a live provider or GTK proof."""
    rows = []
    for file in sorted(profiles_dir.glob("*.toml")):
        profile = tomllib.loads(file.read_text(encoding="utf-8"))
        services = profile.get("services", {})
        if not isinstance(services, dict):
            raise ValueError(f"{file.name}: services invalide")
        available = {name for name, cfg in services.items()
                     if isinstance(cfg, dict) and cfg.get("enabled") is True}
        library = profile.get("library", {})
        identities = profile.get("identities", [])
        if ((isinstance(library, dict) and int(library.get("epubs") or 0) > 0)
                or (isinstance(identities, list) and any(
                    isinstance(row, dict) and int(row.get("epubs") or 0) > 0
                    for row in identities
                ))):
            available.add("local-library")
        specialized = profile.get("specialized", {})
        if isinstance(specialized, dict) and specialized.get("enabled") is True:
            available.add("specialized-seed")
        rows.append((file.stem, available))
    return rows


def build_report(*, matrix_path: Path = MATRIX, packages_dir: Path = PACKAGES,
                 catalog_path: Path = CATALOG, profiles_dir: Path = PROFILES,
                 repo_root: Path | None = None) -> dict:
    repo = repo_root if repo_root is not None else packages_dir.parents[1]
    spec = tomllib.loads(matrix_path.read_text(encoding="utf-8"))
    if spec.get("version") != 1:
        raise ValueError("plugin_coverage.toml doit déclarer version=1")
    declared = spec.get("package", {})
    if not isinstance(declared, dict):
        raise ValueError("La matrice doit déclarer [package.*]")
    catalog_root = tomllib.loads(catalog_path.read_text(encoding="utf-8"))
    catalog = catalog_root.get("service", {})
    if catalog_root.get("version") != 1 or not isinstance(catalog, dict):
        raise ValueError("Catalogue de services invalide")
    if not SERVICE_NAMES.issubset(catalog):
        raise ValueError("Le catalogue de services a changé ; réviser la matrice")
    profile_fixtures = _profile_fixtures(profiles_dir)
    packages = []
    seen = set()
    for file in sorted(packages_dir.glob("*/cifereca-plugin.json")):
        name = file.parent.name
        seen.add(name)
        manifest = json.loads(file.read_text(encoding="utf-8"))
        plugins = manifest.get("plugins", [])
        if not isinstance(plugins, list) or not plugins:
            raise ValueError(f"{name}: plugins du manifeste invalides")
        ids = [p["id"] for p in plugins]
        roles = sorted({p["type"] for p in plugins})
        capabilities = sorted({cap for p in plugins
                               for cap in p.get("capabilities", [])})
        config = declared.get(name, {})
        data = config.get("data", [])
        if (not isinstance(data, list) or len(data) != len(set(data))
                or any(type(d) is not str or d not in DATA_NAMES for d in data)):
            raise ValueError(f"{name}: entrée data invalide")
        served = sorted(service for service, detail in catalog.items()
                        if any(pid in detail.get("plugins", []) for pid in ids))
        fixture_services = sorted(service for service, detail in catalog.items()
                                  if name in detail.get("fixture_packages", []))
        unknown = sorted(set(data) & SERVICE_NAMES - set(served))
        if unknown:
            raise ValueError(f"{name}: service(s) non relié(s) au manifeste : {unknown}")
        evidence = {field: _evidence_paths(repo, name, config, field)
                    for field in EVIDENCE_FIELDS}
        tests = sorted(path.name for path in (file.parent / "tests").glob("test_*.py"))
        fixture_names = set(data) | set(served) | set(fixture_services)
        fixture_profiles = [key for key, available in profile_fixtures
                            if available & fixture_names]
        has_http_role = bool(set(roles) & {"provider.library", "transport"})
        gtk_applicable = bool(set(roles) & {"ui.page", "provider.library"})
        dimensions = {
            "http_fixture": ("partial" if served or fixture_services else
                             "absent" if has_http_role else "not_applicable"),
            "dataset": ("partial" if data or fixture_services else
                        "absent" if gtk_applicable or has_http_role else "not_applicable"),
            "contract": "partial" if evidence["contract_tests"] else "absent",
            "failure_offline": "partial" if evidence["failure_tests"] else "absent",
            "gtk_e2e": ("partial" if evidence["gtk_scenarios"] else
                        "absent" if gtk_applicable else "not_applicable"),
        }
        # Do not claim covered: paths only establish source existence, never
        # execution or an attested run. A local plugin needs no HTTP server.
        status = ("partial" if tests or any(v == "partial" for v in dimensions.values())
                  else "absent")
        packages.append({
            "package": name,
            "manifest_id": manifest["package"]["id"],
            "plugin_ids": ids,
            "roles": roles,
            "capabilities": capabilities,
            "ui_routes": [p["id"] for p in plugins if p.get("type") == "ui.page"],
            "services": served,
            "http_fixtures": fixture_services,
            "data": data,
            "fixture_profiles": fixture_profiles,
            "unit_test_files": len(tests),
            **evidence,
            "dimensions": dimensions,
            "e2e_gtk_verified": False,
            "status": status,
            "gap": config.get("gap", "Non classé"),
        })
    unclassified = sorted(seen - set(declared))
    stale = sorted(set(declared) - seen)
    stats = {
        "packages": len(packages),
        "with_service": sum(bool(r["services"]) for r in packages),
        "with_http_fixture": sum(bool(r["http_fixtures"]) for r in packages),
        "with_data": sum(bool(r["data"]) for r in packages),
        "without_data": sum(not r["data"] for r in packages),
        "with_unit_tests": sum(r["unit_test_files"] > 0 for r in packages),
        "with_contract_scenarios": sum(bool(r["contract_tests"]) for r in packages),
        "with_failure_scenarios": sum(bool(r["failure_tests"]) for r in packages),
        "with_gtk_scenarios": sum(bool(r["gtk_scenarios"]) for r in packages),
        "e2e_gtk_verified": 0,
    }
    return {
        "version": 2,
        "note": ("Inventaire statique des sources et fixtures. Existence de tests "
                 "et de scripts GTK != exécution vérifiée ; aucun statut couvert."),
        "stats": stats,
        "unclassified": unclassified,
        "stale": stale,
        "packages": packages,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="Échec si package non classé/obsolète ou preuve invalide")
    parser.add_argument("--json", action="store_true", help="Rapport JSON")
    args = parser.parse_args(argv)
    try:
        result = build_report()
    except (ValueError, OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for row in result["packages"]:
            status = row["dimensions"]
            print(f"{row['package']:34s} {row['status']:8s} "
                  f"roles={','.join(row['roles']):25s} "
                  f"http={status['http_fixture']:14s} "
                  f"contrat={status['contract']:8s} "
                  f"erreurs={status['failure_offline']:8s} "
                  f"gtk={status['gtk_e2e']:14s} — {row['gap']}")
        print("\nRésumé :", json.dumps(result["stats"], ensure_ascii=False))
        print("Non classés :", result["unclassified"], "obsolètes :", result["stale"])
    return 2 if args.strict and (result["unclassified"] or result["stale"]) else 0


if __name__ == "__main__":
    sys.exit(main())
