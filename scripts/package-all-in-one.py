#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from plugin_packaging import (
    DIST_DIR,
    PLUGINS_DIR,
    PackagingError,
    build_all_in_one,
    discover_plugins,
    sha256_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Construit un bundle ZIP de tous les plugins Cifereca")
    parser.add_argument("--plugins", type=Path, default=PLUGINS_DIR)
    parser.add_argument("--out", type=Path, default=DIST_DIR)
    parser.add_argument("--name", help="nom du ZIP final")
    args = parser.parse_args()
    try:
        output = build_all_in_one(
            discover_plugins(args.plugins),
            args.out,
            filename=args.name,
        )
    except PackagingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(output)
    print(f"sha256: {sha256_file(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
