#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from plugin_packaging import DIST_DIR, PackagingError, build_plugin_zip, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Construit le ZIP installable d'un plugin Cifereca")
    parser.add_argument("plugin", type=Path, help="répertoire plugins/<plugin>")
    parser.add_argument("--out", type=Path, default=DIST_DIR)
    args = parser.parse_args()
    try:
        output = build_plugin_zip(args.plugin, args.out)
    except PackagingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(output)
    print(f"sha256: {sha256_file(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
