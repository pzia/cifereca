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

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "environnements/openai-chat-debian/capture_app.py"
SECRET_KEY = re.compile(r"(token|password|secret|authorization|cookie)", re.I)

SCENARIOS = {
    "startup": {
        "plugin": "core",
        "description": "Démarrage réel de CIFERECA et apparition d'une surface GTK non vide.",
        "args": ["--no-plugins"],
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


def _event(path: Path, scenario: str, event: str, **detail) -> None:
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario,
        "event": event,
        **detail,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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
            home = Path(tmp) / "home"
            home.mkdir()
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "GDK_BACKEND": "x11",
                "PYTHONPATH": str(ROOT / "src"),
            })
            scenario_dir = args.out / name
            scenario_dir.mkdir(parents=True, exist_ok=True)
            png = scenario_dir / "screen.png"
            stdout_path = scenario_dir / "stdout.log"
            stderr_path = scenario_dir / "stderr.log"
            trace_path = scenario_dir / "trace.jsonl"
            _event(trace_path, name, "scenario_start", plugin=scenario["plugin"])
            command = [
                xvfb, "-a", args.python, str(CAPTURE),
                "--startup-timeout", str(args.startup_timeout),
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
            for log_path in (stdout_path, stderr_path):
                log_path.write_text(
                    _redact(log_path.read_text(encoding="utf-8"), env),
                    encoding="utf-8",
                )
            report = png.with_suffix(".json")
            _event(
                trace_path, name, "scenario_end",
                returncode=completed.returncode,
                screenshot=png.exists(),
                report=report.exists(),
            )
            results.append({
                "scenario": name,
                "plugin": scenario["plugin"],
                "returncode": completed.returncode,
                "report": str(report),
                "screenshot": str(png) if png.exists() else None,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "trace": str(trace_path),
            })
            if completed.returncode:
                (args.out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
                return completed.returncode

    (args.out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
