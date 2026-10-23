#!/usr/bin/env python3
"""Prepare an attested wheelhouse for the real full-updater integration gate.

Only exact SHA-pinned PyPI wheels that passed the 168h check are downloaded.
The locally built Cifereca wheel is excluded from the age limit; all runtime
dependencies are included and checked. No managed venv is created by this tool.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cifereca.plugins.python_quarantine import QuarantineRejected, check_pip_report
from cifereca.wheelhouse_quarantine import attest_wheelhouse, verify_wheelhouse


def _run(command: list[str], *, stage: str) -> None:
    result = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, timeout=360, check=False,
    )
    if result.returncode:
        # Tool output can include index URLs and environment state; never echo it.
        print(f"wheelhouse_preflight_failed:{stage}", file=sys.stderr)
        raise QuarantineRejected("dependency_not_eligible")


def _approved_runtime_wheels(requirements: list[str]) -> tuple[str, ...]:
    """Ask pip to resolve transitives; exclude too-new versions and re-resolve."""
    constraints: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="cifereca-admitted-plan-") as tmp:
        temp = Path(tmp)
        report = temp / "plan.json"
        for _attempt in range(16):
            command = [
                sys.executable, "-m", "pip", "--isolated", "install",
                "--disable-pip-version-check", "--dry-run", "--ignore-installed",
                "--no-cache-dir", "--only-binary=:all:", "--report", str(report),
                "--index-url", "https://pypi.org/simple", *requirements,
            ]
            if constraints:
                constraint_file = temp / "constraints.txt"
                constraint_file.write_text(
                    "".join(f"{name}<{version}\n" for name, version in sorted(constraints.items())),
                    encoding="utf-8",
                )
                command += ["--constraint", str(constraint_file)]
            _run(command, stage="resolve")
            try:
                plan = json.loads(report.read_text(encoding="utf-8"))
                return check_pip_report(plan, now=datetime.now(timezone.utc))
            except QuarantineRejected as exc:
                if str(exc) != "dependency_too_recent" or not exc.name or not exc.version:
                    print(f"wheelhouse_preflight_failed:attest:{str(exc)}", file=sys.stderr)
                    raise
                print(f"wheelhouse_quarantined:{exc.name}=={exc.version}", file=sys.stderr)
                # Each retry must strictly reduce at least one allowed version.
                try:
                    if exc.name in constraints and Version(exc.version) >= Version(constraints[exc.name]):
                        raise QuarantineRejected("dependency_not_eligible")
                except InvalidVersion as err:
                    raise QuarantineRejected("dependency_not_eligible") from err
                constraints[exc.name] = exc.version
            except (OSError, ValueError, TypeError) as err:
                raise QuarantineRejected("dependency_provenance_unavailable") from err
    raise QuarantineRejected("dependency_not_eligible")


def _plugin_python_requirements() -> list[str]:
    """Gather plugin direct constraints before creating a release wheelhouse."""
    requirements: list[str] = []
    manifests = sorted((ROOT / "src" / "plugins").glob("*/cifereca-plugin.json"))
    if not manifests:
        raise QuarantineRejected("dependency_not_eligible")
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for descriptor in manifest.get("plugins", []):
            if type(descriptor) is not dict:
                raise QuarantineRejected("dependency_not_eligible")
            for req in descriptor.get("system_requires", []):
                if type(req) is not dict:
                    raise QuarantineRejected("dependency_not_eligible")
                if req.get("type") != "python-package":
                    continue
                name, version = req.get("name"), req.get("version") or ""
                if type(name) is not str or type(version) is not str:
                    raise QuarantineRejected("dependency_not_eligible")
                from packaging.requirements import Requirement
                try:
                    candidate = Requirement(name + version)
                except Exception:
                    raise QuarantineRejected("dependency_not_eligible") from None
                if candidate.url or not candidate.name:
                    raise QuarantineRejected("dependency_not_eligible")
                text = str(candidate)
                if text not in requirements:
                    requirements.append(text)
    return requirements


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-plugin-deps", action="store_true")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["version"] != version:
        raise QuarantineRejected("dependency_not_eligible")
    destination = ROOT / "offline-wheels"
    # This is only a producer-side CI directory, never a user-managed wheelhouse.
    if destination.exists():
        raise QuarantineRejected("dependency_not_eligible")
    destination.mkdir()
    # Verify all runtime dependencies (including every transitive) before pip
    # is permitted to download exact wheel URLs to the release wheelhouse.
    requested = list(project["project"]["dependencies"])
    if args.include_plugin_deps:
        # Official full-source releases bundle every plugin. Resolve their
        # complete *combined* dependency graph, never independently choose
        # wheels which conflict at installation time.
        requested.extend(_plugin_python_requirements())
    urls = _approved_runtime_wheels(requested)
    for url in urls:
        _run([
            sys.executable, "-m", "pip", "--isolated", "download",
            "--disable-pip-version-check", "--no-index", "--no-deps",
            "--no-cache-dir", "--dest", str(destination), url,
        ], stage="download")
    # Building the first-party wheel uses previously provisioned build tools;
    # this script does not install or upgrade those tools.
    _run([
        sys.executable, "-m", "pip", "--isolated", "wheel",
        "--disable-pip-version-check", "--no-deps", "--no-build-isolation",
        "--no-index", "--wheel-dir", str(destination), str(ROOT),
    ], stage="build-core")
    attest_wheelhouse(destination, core_version=version)
    verify_wheelhouse(destination, core_version=version)
    print("admitted_wheelhouse_ready")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (QuarantineRejected, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        reason = str(exc) if isinstance(exc, QuarantineRejected) else "dependency_provenance_unavailable"
        print(reason, file=sys.stderr)
        raise SystemExit(2) from None
