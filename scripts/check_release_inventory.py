"""Validate the source snapshot only at explicit Core release time."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(root: Path) -> None:
    release = json.loads((root / "src/cifereca/plugin-release.json").read_text(encoding="utf-8"))
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if release["release"] != version:
        raise ValueError("Release snapshot version differs from VERSION")
    actual = {}
    for path in sorted((root / "src/plugins").glob("*/cifereca-plugin.json")):
        package = json.loads(path.read_text(encoding="utf-8"))["package"]
        package_id = str(package["id"])
        if package_id in actual:
            raise ValueError(f"Duplicate plugin package: {package_id}")
        actual[package_id] = {"version": str(package["version"]), "name": str(package["name"])}
    if release["packages"] != actual:
        raise ValueError("Release inventory differs from source manifests; synchronize plugin-release.json before publication")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        validate(args.root)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"Release inventory rejected: {error}\n")
    print("Release inventory matches source manifests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
