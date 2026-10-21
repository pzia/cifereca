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

# E2E selection imports this runner by its absolute path (not as a Python
# package). The module directory must be importable in that mode too.
_E2E_DIR = str(Path(__file__).resolve().parent)
if _E2E_DIR not in sys.path:
    sys.path.insert(0, _E2E_DIR)
from gtk_layout_critical import (
    gtk_layout_critical_counts, gtk_layout_critical_failure,
    gtk_layout_phase_counts, gtk_layout_zero_gate,
)

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
    "book-detail-1300": {
        "plugin": "core",
        "fixture": "search-filters",
        "description": "UX #611 : vrai écran X11 1300×768, position et allocation native, Bibliothèque/Recherche sélectionnées, Paned et sidebar.",
        "runner": "gtk-detail",
        "width": 1300,
        "height": 768,
        "geometry_1300": True,
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
    "maintenance-tools-1280": {
        "plugin": "core", "fixture": "startup",
        "description": "P1 #2565 : Maintenance, résultats locaux et limites en GTK/XTEST 1280.",
        "runner": "gtk-maintenance", "width": 1280,
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
    "public-fault-search-1366": {
        "plugin": "core",
        "fixture": "search-filters",
        "description": "P0 #771 : véritable GTK Recherche, HTTP localhost et adapter EbooksGratuits (429/503/retour), statuts anonymes, aucun achat.",
        "runner": "gtk-public-fault",
        "width": 1366,
    },
    "kobo-fault-search-1366": {
        "plugin": "core",
        "fixture": "search-filters",
        "description": "P0 #771 : vrai KoboSession catalogue, HTTP 429/503/200 fictifs, vrai GTK, sans droits ni acquisition.",
        "runner": "gtk-public-fault",
        "width": 1366,
        "provider": "kobo",
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


MAX_E2E_LOG_CHARS = 1_000_000


def _redact(text: str, env: dict[str, str]) -> str:
    """Sanitize in memory, before a CI-readable log file exists."""
    for value in _secret_values(env):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(
        r"(?i)(authorization|token|password|secret|cookie)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)\b(?:https?|ftp|file)://[^\s<>\"']+", "[REDACTED_URL]", text)
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}\b", "[REDACTED_EMAIL]", text)
    text = re.sub(r"(?i)(?<![\w])/(?:home|Users|run/user)/[^\s<>\"']+", "/[REDACTED_PATH]", text)
    return text


def _write_redacted_log(path: Path, text: str, env: dict[str, str]) -> None:
    """Bound untrusted subprocess logs and never write raw streams to evidence."""
    truncated = len(text) > MAX_E2E_LOG_CHARS
    safe = _redact(text[:MAX_E2E_LOG_CHARS], env)
    path.write_text(safe + ("\n[TRUNCATED]\n" if truncated else ""), encoding="utf-8")


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


def _event(trace_path: Path, scenario: str, event: str, **detail) -> None:
    """Persist a bounded, scalar trace; never arbitrary GTK/error payloads."""
    if scenario not in SCENARIOS or event not in {
        "scenario_start", "xvfb_display_retry", "scenario_end",
        "gtk_layout_critical_observed",
    }:
        raise ValueError("unexpected E2E trace event")
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario,
        "event": event,
    }
    if event == "scenario_start" and detail.get("plugin") == SCENARIOS[scenario]["plugin"]:
        row["plugin"] = SCENARIOS[scenario]["plugin"]
    if event == "xvfb_display_retry" and type(detail.get("attempt")) is int:
        row["attempt"] = min(2, max(1, detail["attempt"]))
    if event == "gtk_layout_critical_observed":
        counters = detail.get("gtk_layout_critical")
        if (type(counters) is not dict
                or set(counters) != {"GtkStack", "GtkRevealer", "stream_truncated"}
                or type(counters["GtkStack"]) is not int
                or type(counters["GtkRevealer"]) is not int
                or type(counters["stream_truncated"]) is not bool
                or not 0 <= counters["GtkStack"] <= 100000
                or not 0 <= counters["GtkRevealer"] <= 100000):
            raise ValueError("invalid closed GTK trace counters")
        row["gtk_layout_critical"] = counters
    if event == "scenario_end":
        code = detail.get("returncode")
        if type(code) is int and -255 <= code <= 255:
            row["returncode"] = code
        for key in ("screenshot", "report"):
            if type(detail.get(key)) is bool:
                row[key] = detail[key]
        ratio = detail.get("visual_change_ratio")
        if type(ratio) in (int, float) and 0 <= ratio <= 1:
            row["visual_change_ratio"] = round(ratio, 6)
        # Deliberately drop assertion_error, traceback, URL, nested metadata,
        # private account/session fields, file paths and all unknown keys.
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


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
            elif scenario.get("runner") == "gtk-public-fault":
                command = [
                    xvfb, "-a", "-s", "-screen 0 1366x900x24",
                    args.python, str(ROOT / "e2e/public_fault_search_probe.py"),
                    "--out", str(scenario_dir),
                    *([] if scenario.get("provider") is None else [
                        "--provider", scenario["provider"],
                    ]),
                ]
            elif scenario.get("runner") == "gtk-detail":
                width = scenario["width"]
                display_height = scenario.get("height", 1000 if width == 1600 else 900)
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{display_height}x24",
                    args.python, str(ROOT / "e2e/book_detail_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                    *(["--geometry-1300"] if scenario.get("geometry_1300") else []),
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
            # Collect subprocess output in memory and scrub it before making
            # CI artifact files. Even failed GTK probes cannot persist raw logs.
            completed = subprocess.run(
                command, cwd=ROOT, env=env, check=False,
                capture_output=True, text=True,
            )
            output = completed.stdout or ""
            errors = completed.stderr or ""
            if name in {"startup", "core-navigation"} and completed.returncode:
                # Single Xvfb handover retry: inspect in-memory stdout only.
                if retryable_x11_startup_failure(completed.returncode, output):
                    _event(trace_path, name, "xvfb_display_retry", attempt=2)
                    time.sleep(1)
                    second = subprocess.run(
                        command, cwd=ROOT, env=env, check=False,
                        capture_output=True, text=True,
                    )
                    output += "\n--- X11 display initialization retry (once) ---\n" + (second.stdout or "")
                    errors += "\n--- X11 display initialization retry (once) ---\n" + (second.stderr or "")
                    completed = second
            # Count only fixed GTK widget classes in memory: no raw warning,
            # widget pointer, path or text is added to CI's structured output.
            gtk_critical = gtk_layout_critical_counts(output, errors)
            # These fixed phase counts correlate diagnostic warnings with
            # deterministic GUI actions; none contains timestamps or raw GTK.
            gtk_phases = gtk_layout_phase_counts(output, errors)
            _write_redacted_log(stdout_path, output, env)
            _write_redacted_log(stderr_path, errors, env)
            report = png.with_suffix(".json")
            returncode = completed.returncode
            assertion_error = None
            visual_change_ratio = None
            if report.exists():
                report_data = json.loads(report.read_text(encoding="utf-8"))
                if scenario.get("geometry_1300") and report_data.get("status") != "PASS":
                    stages = {"probe_start", "search_ready", "selected_search", "selected_library",
                              "xwininfo_lookup", "xwininfo_parse", "monitor_and_paned",
                              "strict_native_geometry", "paned_allocations", "checked"}
                    stage = report_data.get("ux611_probe_stage")
                    if type(stage) is str and stage in stages:
                        print("UX611_CLOSED_FAILURE_STAGE=" + stage, flush=True)
                    values = report_data.get("native_1300_geometry")
                    if isinstance(values, dict):
                        for route in ("search", "library"):
                            sample = values.get(route)
                            if not isinstance(sample, dict):
                                continue
                            for family in ("display", "native", "gtk_toplevel", "gdk_surface",
                                           "page", "shell_after_sidebar"):
                                item = sample.get(family)
                                if isinstance(item, dict):
                                    width, height = item.get("width"), item.get("height")
                                    if type(width) is int and type(height) is int and 0 <= width <= 5000 and 0 <= height <= 5000:
                                        print(f"UX611_NUMERIC {route}.{family}={width}x{height}", flush=True)
                visual_change_ratio = report_data.get("visual_change_ratio")
                if scenario.get("runner") in {"gtk-probe", "gtk-diagnostics", "gtk-diagnostics-structured", "gtk-diagnostics-volume", "gtk-diagnostics-states", "gtk-diagnostics-navigation", "gtk-detail", "gtk-llm-settings", "gtk-maintenance", "gtk-constellations", "gtk-recommendations", "gtk-public-fault"} and report_data.get("status") != "PASS":
                    assertion_error = report_data.get("error") or "Recette GTK P0 incomplète"
                    returncode = 1
            minimum_change = scenario.get("min_visual_change_ratio")
            # Keep observed counters on every scenario. After product fix
            # #2466 proved zero criticals in the four former reproductions,
            # those scenarios additionally enforce zero as a regression gate.
            # No log filtering and no raw GTK payloads in public verdicts.
            layout_problem = gtk_layout_critical_failure(gtk_critical)
            if layout_problem:
                _event(trace_path, name, "gtk_layout_critical_observed",
                       gtk_layout_critical=gtk_critical)
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
            if returncode == 0:
                geometry_regression = gtk_layout_zero_gate(name, gtk_critical)
                if geometry_regression is not None:
                    assertion_error = geometry_regression
                    returncode = 1
            _event(
                trace_path, name, "scenario_end",
                returncode=returncode,
                screenshot=png.exists(),
                report=report.exists(),
                visual_change_ratio=visual_change_ratio,
                assertion_error=assertion_error,
                gtk_layout_critical=gtk_critical,
            )
            results.append({
                "scenario": name,
                "plugin": scenario["plugin"],
                "description": scenario["description"],
                "returncode": returncode,
                "visual_change_ratio": visual_change_ratio,
                "assertion_error": assertion_error,
                "gtk_layout_critical": gtk_critical,
                "gtk_layout_phase": gtk_phases if name.startswith(("mainwindow-baseline-", "maintenance-tools-")) else {},
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
