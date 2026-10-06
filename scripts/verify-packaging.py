#!/usr/bin/env python3
from __future__ import annotations

import sys

from plugin_packaging import PackagingError, verify_repository


def main() -> int:
    try:
        rows = verify_repository()
    except PackagingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for row in rows:
        print(
            f"{row['directory']}: {row['package_id']} {row['version']} "
            f"({row['descriptors']} descriptors, {row['files']} files)"
        )
    print(f"OK: {len(rows)} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
