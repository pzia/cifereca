#!/usr/bin/env python3
"""Prepublication cold offline install of the *actual* full release ZIP.

A failure here forbids publication. Existing user installs are never touched.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def run(archive: Path) -> None:
    from cifereca import updater
    with tempfile.TemporaryDirectory(prefix="cifereca-p0-offline-release-") as tmp:
        root = Path(tmp)
        # Independent empty environment: no wheel cache, no access to PyPI.
        locations = {
            "CIFERECA_INSTALL_ROOT": root / "runtime",
            "CIFERECA_BIN_DIR": root / "bin",
            "XDG_CONFIG_HOME": root / "config",
            "XDG_DATA_HOME": root / "data",
            "XDG_STATE_HOME": root / "state",
            "XDG_CACHE_HOME": root / "cache",
            "PIP_CACHE_DIR": root / "pip-cache",
        }
        for key, path in locations.items():
            path.mkdir(parents=True, exist_ok=True)
            os.environ[key] = str(path)
        os.environ["CIFERECA_PLUGIN_DEPS_OFFLINE"] = "1"
        os.environ["PIP_NO_INDEX"] = "1"
        os.environ["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        result = updater.install_archive(archive, python=sys.executable)
        if result.get("ok") is not True or result.get("core_ready") is not True:
            raise AssertionError("cold_offline_core_not_ready")
        if (not isinstance(result.get("plugins"), dict)
                or result["plugins"].get("ok") is not True):
            raise AssertionError("cold_offline_plugins_not_ready")
        current = root / "runtime" / "current"
        if not current.is_symlink():
            raise AssertionError("cold_offline_core_not_active")
        for name in ("cifereca", "cifereca-cli", "cifereca-update"):
            command = current.resolve() / ".venv" / "bin" / name
            proc = subprocess.run(
                [str(command), "--version"], check=False, text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            )
            if proc.returncode:
                raise AssertionError("cold_offline_entrypoint_unavailable")
        print("cold_offline_core_and_plugins_pass", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        run(args.archive.resolve())
    except Exception:
        print("cold_offline_install_failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
