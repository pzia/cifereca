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
    "maintenance-tools-920": {
        "plugin": "core", "fixture": "startup",
        "description": "P0 #2712/#2841 : vrai écran 920×620, parcours Numilog GTK/XTEST et scroll.",
        "runner": "gtk-maintenance", "width": 920, "height": 620,
    },
    "maintenance-tools-1024": {
        "plugin": "core", "fixture": "startup",
        "description": "P0 #2712 : inspection à 1024×768, navigation physique, molette et clavier.",
        "runner": "gtk-maintenance", "width": 1024, "height": 768,
    },
    "maintenance-tools-1280": {
        "plugin": "core", "fixture": "startup",
        "description": "P1 #2565 : Maintenance, résultats locaux et limites en GTK/XTEST 1280.",
        "runner": "gtk-maintenance", "width": 1280, "height": 800,
    },
    "maintenance-tools-1366": {
        "plugin": "core", "fixture": "startup",
        "description": "P0 #1358: vrai GTK/XTEST Outils, lock sync, preview lente, annulation et plan périmé 1366.",
        "runner": "gtk-maintenance", "width": 1366, "height": 768,
    },
    "maintenance-tools-1600": {
        "plugin": "core", "fixture": "startup",
        "description": "P0 #1358: vérification de la confirmation et refus des suppressions GTK 1600.",
        "runner": "gtk-maintenance", "width": 1600,
    },
    "maintenance-tools-1728": {
        "plugin": "core", "fixture": "startup",
        "description": "P0 #2842 : parcours guidé et confirmations natives à 1728×895, sans réseau.",
        "runner": "gtk-maintenance", "width": 1728, "height": 895,
    },
    "recommendations-detail-1024-hidpi": {
        "plugin": "ui.page.recommendations",
        "fixture": "search-filters",
        "description": "P1 #3687/#774 : vraie recette GTK/XTEST HiDPI 2x, 1024×768 logiques dans un Xvfb 2048×1536.",
        "runner": "gtk-recommendations",
        "width": 1024, "height": 768, "scale": 2,
    },
    "recommendations-detail-1024": {
        "plugin": "ui.page.recommendations",
        "fixture": "search-filters",
        "description": "P1 #3687/#774 : Recommandations GTK/XTEST 1024×768 effectifs ; filtres, compteur et clavier.",
        "runner": "gtk-recommendations",
        "width": 1024, "height": 768,
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
    "optimizer-handoff-1366": {
        "plugin": "ui.page.epub-optimizer", "fixture": "startup",
        "description": "P1 #2800 : Optimiseur préconstruit, vrais clics GTK/XTEST A→retour→B, verrou busy à 1366.",
        "runner": "gtk-optimizer-handoff", "width": 1366,
    },
    "optimizer-handoff-1600": {
        "plugin": "ui.page.epub-optimizer", "fixture": "startup",
        "description": "P1 #2800 : Optimiseur préconstruit, vrais clics GTK/XTEST A→retour→B, verrou busy à 1600.",
        "runner": "gtk-optimizer-handoff", "width": 1600,
    },
    "diagnostics-1366": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "P0 #1011 : vrai widget GTK Diagnostics 1366, brokers isolés, JSON, rapport, pagination, sans LLM.",
        "runner": "gtk-diagnostics",
        "width": 1366,
    },
    "diagnostics-raw-export-1366": {
        "plugin": "ui.page.diagnostics",
        "fixture": "startup",
        "description": "P1 #3218 : GTK4 SAVE natif, répertoire local, Annuler, puis export brut exact en 0600.",
        "runner": "gtk-diagnostics-raw-export",
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
    "capture-session-gtk-1366": {
        "plugin": "core",
        "fixture": "startup",
        "description": "P1 #4093: vraie GUI --capture, fermeture GTK normale et ZIP local 0600, sans compte.",
        "runner": "gtk-capture-session",
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
    "bnf-covers-1366": {
        "plugin": "provider.bnf-pret",
        "fixture": "search-filters",
        "description": "P1 #4118 : 2 couvertures BnF visibles en Recherche et Détail GTK, une notice sans image ; transport HTTPX isolé.",
        "runner": "gtk-bnf-covers",
        "width": 1366,
    },
    "baen-fault-search-1366": {
        "plugin": "core",
        "fixture": "search-filters",
        "description": "P1 #2332 : vrai BaenSession + loopback 429/503/200 partiel ; aucun téléchargement.",
        "runner": "gtk-public-fault",
        "width": 1366,
        "provider": "baen",
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
                height = scenario.get("height", 1000 if width == 1600 else 900)
                scale = scenario.get("scale", 1)
                # GTK measures logical units while XTEST and ImageGrab see
                # physical pixels. Never emulate HiDPI by merely enlarging
                # the window on an unscaled display.
                env["GDK_SCALE"] = str(scale)
                command = [
                    xvfb, "-a", "-s",
                    f"-screen 0 {width * scale}x{height * scale}x24",
                    args.python, str(ROOT / "e2e/recommendations_detail_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                    "--scale", str(scale),
                ]
            elif scenario.get("runner") == "gtk-constellations":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/constellations_detail_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-optimizer-handoff":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s",
                    f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/optimizer_handoff_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-diagnostics":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/diagnostics_page_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-diagnostics-raw-export":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x900x24",
                    args.python, str(ROOT / "e2e/diagnostics_raw_export_probe.py"),
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
                    xvfb, "-a", "-s", f"-screen 0 {width}x{scenario.get('height', 1000 if width == 1600 else 900)}x24",
                    args.python, str(ROOT / "e2e/maintenance_tools_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                    "--display-height", str(scenario.get("height", 1000 if width == 1600 else 900)),
                ]
            elif scenario.get("runner") == "gtk-llm-settings":
                width = scenario["width"]
                command = [
                    xvfb, "-a", "-s", f"-screen 0 {width}x{1000 if width == 1600 else 900}x24",
                    args.python, str(ROOT / "e2e/llm_settings_probe.py"),
                    "--out", str(scenario_dir), "--width", str(width),
                ]
            elif scenario.get("runner") == "gtk-capture-session":
                command = [
                    xvfb, "-a", "-s", "-screen 0 1366x900x24",
                    args.python, str(ROOT / "e2e/capture_session_gtk_probe.py"),
                    "--out", str(scenario_dir), "--width", "1366",
                ]
            elif scenario.get("runner") == "gtk-bnf-covers":
                command = [
                    xvfb, "-a", "-s", "-screen 0 1366x900x24",
                    args.python, str(ROOT / "e2e/bnf_cover_probe.py"),
                    "--out", str(scenario_dir),
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
                # #774: export only closed phase codes and bounded numeric
                # XTEST geometry for the synthetic Recommendations probe.
                # Raw exceptions, search text and GTK widget labels stay local.
                if (name in {"recommendations-detail-1024-hidpi",
                             "recommendations-detail-1024",
                             "recommendations-detail-1366",
                             "recommendations-detail-1600"}
                        and report_data.get("status") != "PASS"):
                    allowed = frozenset({
                        "setup", "display_geometry", "gtk_geometry", "image_pixels",
                        "keyboard_focus", "keyboard_type", "keyboard_verdict",
                        "original_scenarios",
                    })
                    phase = report_data.get("p1_774_phase")
                    if type(phase) is str and phase in allowed:
                        print("P1_774_CLOSED_FAILURE_STAGE=" + phase, flush=True)
                    # Keep only a bounded numeric source line from our own
                    # fixed synthetic probe. No traceback or path is exported.
                    line = report_data.get("p1_774_probe_line")
                    if type(line) is int and 1 <= line <= 1200:
                        print("P1_774_PROBE_LINE=" + str(line), flush=True)
                    dimensions = report_data.get("p1_774_geometry")
                    if type(dimensions) is dict and set(dimensions) == {
                        "display_width", "display_height",
                        "window_width", "window_height",
                    } and all(type(value) is int and 0 <= value <= 4096
                              for value in dimensions.values()):
                        for key in sorted(dimensions):
                            print("P1_774_NUMERIC_" + key + "=" +
                                  str(dimensions[key]), flush=True)
                    # Synthetic keyboard verdict: bounded boolean flags only.
                    # Never export the actual query, summary or widget text.
                    coherence = report_data.get("p1_774_keyboard_coherence")
                    coherence_keys = frozenset({
                        "text", "rows", "title", "count", "summary_title", "retries",
                    })
                    if (type(coherence) is dict and set(coherence) == coherence_keys
                            and all(type(coherence[k]) is int and coherence[k] in (0, 1)
                                    for k in coherence_keys - {"retries"})
                            and type(coherence["retries"]) is int
                            and 0 <= coherence["retries"] <= 30):
                        for key in sorted(coherence_keys):
                            print("P1_774_KEYBOARD_NUMERIC_" + key + "=" +
                                  str(coherence[key]), flush=True)
                    # Closed diagnostic shape for the synthetic Recommandations
                    # summary; never export raw labels or filter values.
                    shape = report_data.get("p1_774_keyboard_summary_shape")
                    shape_keys = frozenset({
                        "events", "ever_count", "ever_title", "ever_order",
                        "current_order", "current_provisional",
                        "current_recommendation", "ranked", "matched",
                        "label_length", "unexpected_actions",
                    })
                    counts = {"events": 64, "ranked": 100, "matched": 100,
                              "label_length": 512, "unexpected_actions": 10}
                    if (type(shape) is dict and set(shape) == shape_keys
                            and all(type(shape[key]) is int
                                    and 0 <= shape[key] <= counts.get(key, 1)
                                    for key in shape_keys)):
                        for key in sorted(shape_keys):
                            print("P1_774_SUMMARY_SHAPE_" + key + "=" +
                                  str(shape[key]), flush=True)
                    # Cover rendering at HiDPI: numeric observations only.
                    # Never export image URLs, account data, titles or screenshots.
                    cover = report_data.get("p1_774_cover_state")
                    cover_keys = frozenset({
                        "attempt", "list_found", "list_mapped", "list_paintable",
                        "detail_found", "detail_mapped", "detail_paintable",
                        "image_requests", "list_stack_mapped", "detail_pane_mapped",
                    })
                    if (type(cover) is dict and set(cover) == cover_keys
                            and all(type(cover[key]) is int
                                    and 0 <= cover[key] <= (
                                        1 if key in {"list_stack_mapped", "detail_pane_mapped"}
                                        else 30 if key == "attempt" else 20
                                    ) for key in cover_keys)):
                        for key in sorted(cover_keys):
                            print("P1_774_COVER_NUMERIC_" + key + "=" +
                                  str(cover[key]), flush=True)
                    # Closed aggregate counters from the synthetic CoverLoader.
                    # No URL, filesystem path, image bytes or account data.
                    loader_state = report_data.get("p1_774_cover_loader_state")
                    loader_keys = frozenset({
                        "requested", "cache_hit_valid", "download_ok",
                        "download_failed", "preview_created", "preview_hit",
                        "preview_fallback", "ui_applied", "ui_stale", "ui_failed",
                        "decode_failed", "cancelled_callbacks", "queue",
                        "inflight_urls", "preview_source_none",
                        "preview_worker_skipped", "preview_no_future",
                        "preview_result_none", "preview_result_path",
                        "preview_callback_suppressed",
                    })
                    if (type(loader_state) is dict
                            and set(loader_state) == loader_keys
                            and all(type(loader_state[key]) is int
                                    and 0 <= loader_state[key] <= 100
                                    for key in loader_keys)):
                        for key in sorted(loader_keys):
                            print("P1_774_LOADER_NUMERIC_" + key + "=" +
                                  str(loader_state[key]), flush=True)
                    # Closed numeric binding state: whitelist all nested keys
                    # and bounds, never expose URL, callback, file or labels.
                    bindings = report_data.get("p1_774_cover_bind_state")
                    bound_keys = frozenset({
                        "count", "generation", "expected_url", "same_loader",
                        "request_present", "request_active", "source_present",
                        "source_active", "spinner", "placeholder",
                    })
                    if (type(bindings) is dict
                            and set(bindings) == {"list", "detail"}
                            and all(type(row) is dict and set(row) == bound_keys
                                    and all(type(row[key]) is int and
                                            0 <= row[key] <= (
                                                100 if key == "generation"
                                                else 20 if key == "count"
                                                else 1)
                                            for key in bound_keys)
                                    for row in bindings.values())):
                        for part in ("list", "detail"):
                            for key in sorted(bound_keys):
                                print("P1_774_BIND_NUMERIC_" + part + "_" + key +
                                      "=" + str(bindings[part][key]), flush=True)
                    # Eight screen-local bounded integers diagnose a clipped
                    # overflow button without leaking widget text, session,
                    # actions, account metadata or raw GTK tracebacks.
                    overflow = report_data.get("p1_774_overflow_geometry")
                    allowed_overflow = frozenset({
                        "menu_x", "menu_y", "menu_width", "menu_height",
                        "bar_x", "bar_y", "bar_width", "bar_height",
                    })
                    if (type(overflow) is dict
                            and set(overflow) == allowed_overflow
                            and all(type(n) is int and -4096 <= n <= 4096
                                    for n in overflow.values())):
                        for key in sorted(allowed_overflow):
                            print("P1_774_OVERFLOW_NUMERIC_" + key + "=" +
                                  str(overflow[key]), flush=True)
                # #4673: a fixed checkpoint code from our own synthetic GTK
                # probe is the only failure detail emitted to public Actions
                # logs. Never echo report.error, widget labels or tracebacks.
                if name == "search-filter-p0" and report_data.get("status") != "PASS":
                    allowed = frozenset({
                        "fixture_setup", "initial_camille", "query_camille",
                        "await_camille", "action_camille",
                        "admission_camille", "button_camille", "holdings_camille",
                        "switch_alex", "query_alex", "await_alex", "action_alex",
                        "admission_alex", "button_alex", "holdings_alex",
                        "disconnect_alex", "clear_disconnected",
                        "finish_disconnected", "return_camille", "complete",
                    })
                    stage = report_data.get("p0_4673_stage")
                    if type(stage) is str and stage in allowed:
                        print("P0_4673_CLOSED_FAILURE_STAGE=" + stage, flush=True)
                    if stage in {
                        "admission_camille", "admission_alex",
                        "button_camille", "button_alex",
                    }:
                        local_reasons = frozenset({
                            "local_action_available_provider_unknown",
                            "primary_paris_connection_mismatch_or_inactive",
                            "local_edition_scope_invalid",
                            "local_provider_or_identity_unavailable",
                            "local_session_unavailable",
                            "local_primary_account_unverified",
                            "local_claim_scope_invalid",
                            "local_previous_attempt",
                            "local_scope_changed",
                            "local_borrow_capability_unavailable",
                            "local_borrow_executor_unavailable",
                            "local_admission_unavailable",
                        })
                        code = report_data.get("p0_4673_local_reason")
                        if type(code) is str and code in local_reasons:
                            print("P0_4673_CLOSED_LOCAL_REASON=" + code, flush=True)
                if (name in {"recommendations-detail-1024-hidpi",
                             "recommendations-detail-1024",
                             "recommendations-detail-1366",
                             "recommendations-detail-1600"}
                        and report_data.get("status") != "PASS"):
                    # Source probe stages are a reviewed fixed vocabulary;
                    # raw GTK error/callback output remains private.
                    prior_stages = frozenset({
                        "probe_start", "initial", "long_detail", "scrolled",
                        "exact_setup", "exact_button", "exact_overflow",
                        "exact_overflow_ready", "exact_focus", "exact_key",
                        "exact_page_focus", "exact_page_key", "exact_page_verdict",
                        "exact_verdict", "exact_key_dispatch", "exact_navigation",
                        "exact_selection", "exact_no_search", "return_list",
                    })
                    prior = report_data.get("p1_1354_phase")
                    if type(prior) is str and prior in prior_stages:
                        print("P1_1354_CLOSED_FAILURE_STAGE=" + prior, flush=True)
                    # Only the reviewed, fixed synthetic checkpoint can leave
                    # the private GTK sandbox. No assertion, title or source.
                    allowed = frozenset({
                        "setup", "button", "button_mapped", "button_selection",
                        "button_jobs", "button_focus", "key", "holdings",
                        "download", "obtain", "unavailable",
                    })
                    stage = report_data.get("p1_4664_phase")
                    if type(stage) is str and stage in allowed:
                        print("P1_4664_CLOSED_FAILURE_STAGE=" + stage, flush=True)
                visual_change_ratio = report_data.get("visual_change_ratio")
                if scenario.get("runner") in {"gtk-probe", "gtk-diagnostics", "gtk-diagnostics-raw-export", "gtk-diagnostics-structured", "gtk-diagnostics-volume", "gtk-diagnostics-states", "gtk-diagnostics-navigation", "gtk-detail", "gtk-llm-settings", "gtk-maintenance", "gtk-constellations", "gtk-recommendations", "gtk-public-fault", "gtk-bnf-covers", "gtk-optimizer-handoff", "gtk-capture-session"} and report_data.get("status") != "PASS":
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
