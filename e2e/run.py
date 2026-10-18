#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "environnements/openai-chat-debian/capture_app.py"
SECRET_KEY = re.compile(r"(token|password|secret|authorization|cookie)", re.I)

SCENARIOS = {
    "search-filter-p0": {
        "plugin": "core",
        "fixture": "search-filters",
        "description": (
            "P0 #660 : vraie recherche Camille, XTEST ouverture/fermeture des cinq "
            "filtres GTK ; requête, 36 résultats, compteur, sélection et scroll inchangés."
        ),
        "runner": "gtk-probe",
    },
    "book-detail-1366": {
        "plugin": "core",
        "fixture": "search-filters",
        "description": "P1 #942 : vrai Détail bibliographique repliable Recherche/Bibliothèque GTK 1366.",
        "runner": "gtk-detail",
        "width": 1366,
    },
    "book-detail-1600": {
        "plugin": "core",
        "fixture": "search-filters",
        "description": "P1 #1598 : même fiche partagée Recherche/Bibliothèque, auteurs multivalués et actions GTK 1600.",
        "runner": "gtk-detail",
        "width": 1600,
    },
    "mainwindow-baseline-1366": {
        "plugin": "core", "fixture": "search-filters",
        "description": "P0 #1123: MainWindow, async navigation, keyboard, two identities and normal close.",
        "runner": "gtk-diagnostics-navigation", "width": 1366,
        "slow_check": True, "window_baseline": True,
    },
    "mainwindow-baseline-1600": {
        "plugin": "core", "fixture": "search-filters",
        "description": "P0 #1123: same MainWindow contracts at 1600x1000.",
        "runner": "gtk-diagnostics-navigation", "width": 1600,
        "slow_check": True, "window_baseline": True,
    },
    "llm-settings-1366": {
        "plugin": "core",
        "fixture": "startup",
        "description": "P0 #1127: paramètres IA, consentement, annulation et fermeture (GTK/XTEST 1366).",
        "runner": "gtk-llm-settings",
        "width": 1366,
    },
    "llm-settings-1600": {
        "plugin": "core",
        "fixture": "startup",
        "description": "P0 #1127: mêmes gardes et focus Paramètres IA (GTK/XTEST 1600).",
        "runner": "gtk-llm-settings",
        "width": 1600,
    },
    "maintenance-tools-1366": {
        "plugin": "core", "fixture": "startup",
        "description": "P0 #1358: vrai GTK/XTEST Outils, lock sync, preview lente, annulation et plan périmé 1366.",
        "runner": "gtk-maintenance", "width": 1366,
    },
    "maintenance-tools-1600": {
        "plugin": "core", "fixture": "startup",
        "description": "P0 #1358: vérification de la confirmation et refus des suppressions GTK 1600.",
        "runner": "gtk-maintenance", "width": 1600,
    },
    "recommendations-detail-1366": {
        "plugin": "ui.page.recommendations",
        "fixture": "search-filters",
        "description": "P1 #1599 : vrai Recommandations GTK/XTEST 1366, 0/1/N, sélection et ISBN local.",
        "runner": "gtk-recommendations",
        "width": 1366,
    },
    "recommendations-detail-1600": {
        "plugin": "ui.page.recommendations",
        "fixture": "search-filters",
        "description": "P1 #1599 : vrai Recommandations GTK/XTEST 1600, changement de profil et action périmée.",
        "runner": "gtk-recommendations",
        "width": 1600,
    },
    "constellations-detail-1366": {
        "plugin": "ui.page.constellations",
        "fixture": "search-filters",
        "description": "P1 #1599 : vrai plugin Constellations, hôte public et navigation GTK/XTEST 1366.",
        "runner": "gtk-constellations",
        "width": 1366,
    },
    "constellations-detail-1600": {
        "plugin": "ui.page.constellations",
        "fixture": "search-filters",
        "description": "P1 #1599 : même vrai GTK/XTEST à 1600, identités et actions périmées.",
        "runner": "gtk-constellations",
        "width": 1600,
    },
    "youth-1366": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "P1 #758 : page Jeunesse GTK réelle, XTEST, 1366px, âges, identité, provenance.",
        "runner": "gtk-youth",
        "width": 1366,
    },
    "youth-1600": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "Plugins P1 #1599 : vraie page Jeunesse GTK/XTEST 1600px ; détails, identités, retour et sélection.",
        "runner": "gtk-youth",
        "width": 1600,
    },
    "youth-filter-help-1366": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "UX P1 #922 : 10 aides et noms permanents des filtres Jeunesse sur la vraie GTK 1366.",
        "runner": "gtk-youth",
        "width": 1366,
        "filters_ux": True,
    },
    "youth-milan-1366": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "P1 #758 : vrai Milan activé, pagination, lien notice GTK et panne HTTP simulée à 1366px.",
        "runner": "gtk-youth",
        "width": 1366,
        "milan": True,
    },
    "youth-editions-1366": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "P1 #758 : deux éditions même titre/ISBN différents, âges contradictoires par source, vrai GTK 1366.",
        "runner": "gtk-youth",
        "width": 1366,
        "editions": True,
    },
    "youth-editions-1600": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "P1 #1599 : deux éditions, ISBN distincts et âges par provenance dans le vrai détail GTK 1600.",
        "runner": "gtk-youth",
        "width": 1600,
        "editions": True,
    },
    "youth-focus-return-1366": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "UX P2 #962 : restaurer le focus du bouton Jeunesse après Retour depuis Recherche (GTK/XTEST 1366).",
        "runner": "gtk-youth",
        "width": 1366,
        "library_search": True,
        "focus_return": True,
    },
    "youth-library-search-1366": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "P0 #906 UX : Jeunesse -> Recherche connue -> Retour par vraie GTK/XTEST 1366.",
        "runner": "gtk-youth",
        "width": 1366,
        "library_search": True,
    },
    "youth-library-search-1600": {
        "plugin": "catalog.youth",
        "fixture": "search-filters",
        "description": "P1 #1599 : Jeunesse -> Recherche connue -> Retour, sélection et clavier XTEST 1600.",
        "runner": "gtk-youth",
        "width": 1600,
        "library_search": True,
    },
    "diagnostics-1366": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "P0 #1011 : vrai widget GTK Diagnostics 1366, brokers isolés, JSON, rapport, pagination, sans LLM.",
        "runner": "gtk-diagnostics",
        "width": 1366,
    },
    "diagnostics-large-1600": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "P0 #1011 : vrai GTK 1600x1000, rapport support multipage, scroll indépendant, Tab/Shift+Tab/Space XTEST.",
        "runner": "gtk-diagnostics",
        "width": 1600,
    },
    "diagnostics-structured-1366": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "P0 #1011 D07/D13 : types JSON null/false/0, Unicode, secrets expurgés, sélection GTK et Ctrl+C XTEST.",
        "runner": "gtk-diagnostics-structured",
        "width": 1366,
    },
    "diagnostics-volume-1366": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "P0 #1011 D06/D08-D12 : 10000 événements fictifs paginés, filtres source/module/période et provenance sans réseau.",
        "runner": "gtk-diagnostics-volume",
        "width": 1366,
    },
    "diagnostics-states-1366": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "P0 #1011 D01-D04 : états de sources GTK, broker indisponible, retry et contrôle actif lent",
        "runner": "gtk-diagnostics-states",
        "width": 1366,
    },
    "diagnostics-navigation-slow-1366": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "D04 : vraie MainWindow, clic XTEST vérification lente, navigation Bibliothèque et retour",
        "runner": "gtk-diagnostics-navigation",
        "width": 1366,
        "slow_check": True,
    },
    "diagnostics-navigation-1366": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "P0 #1011 : vraie MainWindow Bibliothèque → Diagnostics → Bibliothèque via XTEST, brokers isolés.",
        "runner": "gtk-diagnostics-navigation",
        "width": 1366,
    },
    "startup": {
        "plugin": "core",
        "fixture": "startup",
        "description": "Démarrage réel de CIFERECA et apparition d'une surface GTK non vide.",
        "args": ["--no-plugins"],
        "capture_args": ["--wait", "1.0"],
    },
    "core-navigation": {
        "plugin": "core",
        "fixture": "startup",
        "description": "Interaction X11 réelle depuis la Bibliothèque familiale vers la page Recherche.",
        "args": ["--no-plugins"],
        "capture_args": [
            "--wait", "0.75",
            "--click", "90,204",
            "--settle-after-actions", "2.0",
            "--wait-change-ratio", "0.05",
        ],
        "min_visual_change_ratio": 0.05,
    },
}


def select(name: str | None, plugin: str | None) -> list[tuple[str, dict]]:
    rows = list(SCENARIOS.items())
    if name:
        rows = [(key, value) for key, value in rows if key == name]
    if plugin:
        rows = [(key, value) for key, value in rows if value["plugin"] == plugin]
    if not rows:
        raise SystemExit("Aucun scénario E2E ne correspond au filtre demandé.")
    return rows


def _secret_values(env: dict[str, str]) -> list[str]:
    return sorted(
        {value for key, value in env.items() if value and SECRET_KEY.search(key) and len(value) >= 4},
        key=len,
        reverse=True,
    )


def _redact(text: str, env: dict[str, str]) -> str:
    for value in _secret_values(env):
        text = text.replace(value, "[REDACTED]")
    return re.sub(
        r"(?i)(authorization|token|password|secret|cookie)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        text,
    )


def materialize_fixture(name: str, root: Path) -> dict[str, str]:
    fixture = ROOT / "e2e" / "fixtures" / name / "config.json"
    if not fixture.is_file():
        raise RuntimeError(f"Fixture E2E absente: {fixture}")
    home = root / "home"
    library = home / "EBooks" / "e2e"
    library.mkdir(parents=True, exist_ok=True)
    config_home = root / "config"
    data_home = root / "data"
    cache_home = root / "cache"
    state_home = root / "state"
    for path in (config_home, data_home, cache_home, state_home):
        path.mkdir(parents=True, exist_ok=True)
    payload = fixture.read_text(encoding="utf-8").replace("${E2E_LIBRARY}", str(library))
    target = config_home / "cifereca" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_DATA_HOME": str(data_home),
        "XDG_CACHE_HOME": str(cache_home),
        "XDG_STATE_HOME": str(state_home),
    }


def _event(path: Path, scenario: str, event: str, **detail) -> None:
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario,
        "event": event,
        **detail,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def retryable_x11_startup_failure(returncode: int, output: str) -> bool:
    """One retry only for Xvfb/GDK display initialization (never test assertions)."""
    return (
        returncode != 0
        and "Gtk couldn't be initialized" in output
        and "GDK_IS_DISPLAY" in output
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Runner E2E GTK reproductible CIFERECA")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--plugin", help="Ne lancer que les scénarios associés à ce plugin/domaine")
    parser.add_argument("--out", type=Path, default=ROOT / "e2e-artifacts")
    parser.add_argument("--python", default=os.environ.get("CIFERECA_E2E_PYTHON", sys.executable))
    parser.add_argument("--startup-timeout", type=float, default=12.0)
    args = parser.parse_args()

    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        raise SystemExit("xvfb-run est requis pour les E2E GTK.")

    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for name, scenario in select(args.scenario, args.plugin):
        with tempfile.TemporaryDirectory(prefix="cifereca-e2e-") as tmp:
            fixture_env = materialize_fixture(scenario["fixture"], Path(tmp))
            env = os.environ.copy()
            env.update(fixture_env)
            env.update({
                "GDK_BACKEND": "x11",
                "PYTHONPATH": str(ROOT / "src"),
                "CODEX_HEADLESS_SCREEN": "1280x1024x24",
            })
            scenario_dir = args.out / name
            scenario_dir.mkdir(parents=True, exist_ok=True)
            png = scenario_dir / "screen.png"
            stdout_path = scenario_dir / "stdout.log"
            stderr_path = scenario_dir / "stderr.log"
            trace_path = scenario_dir / "trace.jsonl"
            _event(trace_path, name, "scenario_start", plugin=scenario["plugin"])
            if scenario.get("runner") == "gtk-recommendations":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/recommendations_detail_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-constellations":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/constellations_detail_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-youth":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x900x24",
                    args.python, str(ROOT / "e2e/youth_page_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                    *(["--milan"] if scenario.get("milan") else []),
                    *(["--editions"] if scenario.get("editions") else []),
                    *(["--library-search"] if scenario.get("library_search") else []),
                    *(["--focus-return"] if scenario.get("focus_return") else []),
                    *(["--filters-ux"] if scenario.get("filters_ux") else []),
                ]
            elif scenario.get("runner") == "gtk-diagnostics":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/diagnostics_page_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-diagnostics-structured":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x900x24",
                    args.python, str(ROOT / "e2e/diagnostics_structured_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-diagnostics-volume":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x900x24",
                    args.python, str(ROOT / "e2e/diagnostics_volume_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-diagnostics-states":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x900x24",
                    args.python, str(ROOT / "e2e/diagnostics_states_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-diagnostics-navigation":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/diagnostics_navigation_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                    *(["--slow-check"] if scenario.get("slow_check") else []),
                    *(["--window-baseline"] if scenario.get("window_baseline") else []),
                ]
            elif scenario.get("runner") == "gtk-maintenance":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/maintenance_tools_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-llm-settings":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/llm_settings_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-detail":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/book_detail_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-probe":
                command = [
                    xvfb, "-a", "-s", "-screen 0 1280x1024x24",
                    args.python, str(ROOT / "e2e/search_filter_probe.py"),
                    "--out", str(scenario_dir),
                ]
            else:
                command = [
                    xvfb, "-a", "-s", "-screen 0 1280x1024x24", args.python, str(CAPTURE),
                    "--startup-timeout", str(args.startup_timeout),
                    *scenario.get("capture_args", []),
                    "--out", str(png),
                    "--cwd", str(ROOT),
                    "--",
                    args.python, "-m", "cifereca", *scenario["args"],
                ]
            with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
                completed = subprocess.run(
                    command, cwd=ROOT, env=env, check=False,
                    stdout=stdout, stderr=stderr, text=True,
                )
                # An occasional Xvfb handover race can leave GTK without a
                # display (GDK_IS_DISPLAY), even when the previous GTK
                # scenario passed. Retry exactly once in a new Xvfb session,
                # retaining the original log and recording the retry.
                if name in {"startup", "core-navigation"} and completed.returncode:
                    stdout.flush()
                    first_output = stdout_path.read_text(encoding="utf-8")
                    if retryable_x11_startup_failure(completed.returncode, first_output):
                        _event(trace_path, name, "xvfb_display_retry", attempt=2)
                        stdout.write("\n--- X11 display initialization retry (once) ---\n")
                        stdout.flush()
                        time.sleep(1)
                        completed = subprocess.run(
                            command, cwd=ROOT, env=env, check=False,
                            stdout=stdout, stderr=stderr, text=True,
                        )
            for log_path in (stdout_path, stderr_path):
                log_path.write_text(
                    _redact(log_path.read_text(encoding="utf-8"), env),
                    encoding="utf-8",
                )
            report = png.with_suffix(".json")
            returncode = completed.returncode
            assertion_error = None
            visual_change_ratio = None
            if report.exists():
                report_data = json.loads(report.read_text(encoding="utf-8"))
                visual_change_ratio = report_data.get("visual_change_ratio")
                if scenario.get("runner") in {"gtk-probe", "gtk-youth", "gtk-diagnostics", "gtk-diagnostics-structured", "gtk-diagnostics-volume", "gtk-diagnostics-states", "gtk-diagnostics-navigation", "gtk-detail", "gtk-llm-settings", "gtk-maintenance", "gtk-constellations", "gtk-recommendations"} and report_data.get("status") != "PASS":
                    assertion_error = report_data.get("error") or "Recette GTK P0 incomplète"
                    returncode = 1
            minimum_change = scenario.get("min_visual_change_ratio")
            if returncode == 0 and minimum_change is not None:
                if visual_change_ratio is None:
                    assertion_error = "Le rapport de capture ne contient pas visual_change_ratio."
                elif visual_change_ratio < minimum_change:
                    assertion_error = (
                        f"Navigation non observée: visual_change_ratio={visual_change_ratio:.4f} "
                        f"< {minimum_change:.4f}."
                    )
                if assertion_error:
                    returncode = 1
                    with stderr_path.open("a", encoding="utf-8") as stderr:
                        stderr.write(assertion_error + "\n")
            _event(
                trace_path, name, "scenario_end",
                returncode=returncode,
                screenshot=png.exists(),
                report=report.exists(),
                visual_change_ratio=visual_change_ratio,
                assertion_error=assertion_error,
            )
            results.append({
                "scenario": name,
                "plugin": scenario["plugin"],
                "description": scenario["description"],
                "returncode": returncode,
                "visual_change_ratio": visual_change_ratio,
                "assertion_error": assertion_error,
                "report": str(report),
                "screenshot": str(png) if png.exists() else None,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "trace": str(trace_path),
            })
            if returncode:
                (args.out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
                return returncode

    (args.out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
