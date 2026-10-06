#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from plugin_packaging import (
    DIST_DIR,
    PLUGINS_DIR,
    PackagingError,
    build_dev_snapshot,
    discover_plugins,
    sha256_file,
    validate_with_update_dev,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Construit un snapshot plugins accepté par cifereca-update-dev Core044"
    )
    parser.add_argument("--plugins", type=Path, default=PLUGINS_DIR)
    parser.add_argument("--out", type=Path, default=DIST_DIR)
    parser.add_argument("--id", dest="snapshot_id", help="id/racine du snapshot")
    parser.add_argument("--name", help="nom du ZIP final")
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="demande de ne pas activer les nouveaux packages",
    )
    parser.add_argument(
        "--validate-with-update-dev",
        action="store_true",
        help="exécute cifereca-update-dev <snapshot> --dry-run --json",
    )
    parser.add_argument(
        "--update-dev",
        default="cifereca-update-dev",
        help="exécutable de validation réelle",
    )
    args = parser.parse_args()
    try:
        output = build_dev_snapshot(
            discover_plugins(args.plugins),
            args.out,
            snapshot_id=args.snapshot_id,
            filename=args.name,
            activate=not args.no_activate,
        )
        if args.validate_with_update_dev:
            validate_with_update_dev(output, executable=args.update_dev)
    except PackagingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(output)
    print(f"sha256: {sha256_file(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
