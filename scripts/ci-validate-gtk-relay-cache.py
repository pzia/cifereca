#!/usr/bin/env python3
"""Build an allowlisted, privacy-safe Debian GTK dependency relay (#1143).

Never upload an unchecked cache tree, a checkout tarball, log or diagnostic.
The Debian packages still require a trusted APT download source; this guard
only enforces the artifact boundary and refuses unexpected local files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tarfile

MARKER = ".gtk4-cache-profile.json"
PROFILE = {"distro": "debian", "codename": "trixie", "architecture": "amd64"}
DEB_NAME = re.compile(r"[a-z0-9][a-z0-9+._~-]*_[a-zA-Z0-9.+:~_-]+_(?:all|amd64)\.deb")
MAX_MARKER_BYTES = 512
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MIN_PACKAGES, MAX_PACKAGES = 11, 300


def build(cache: Path, output: Path) -> int:
    if cache.is_symlink() or not cache.is_dir():
        raise ValueError("invalid cache")
    marker = cache / MARKER
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > MAX_MARKER_BYTES:
        raise ValueError("invalid cache profile")
    if json.loads(marker.read_text(encoding="utf-8")) != PROFILE:
        raise ValueError("unexpected cache profile")

    packages: list[Path] = []
    total = 0
    for path in cache.rglob("*"):
        relative = path.relative_to(cache)
        if path.is_symlink():
            raise ValueError("symlink in cache")
        if path.is_dir():
            # APT may leave its empty partial-download directory behind.
            if relative.parts != ("partial",):
                raise ValueError("unexpected cache directory")
            continue
        if not path.is_file() or len(relative.parts) != 1:
            raise ValueError("unexpected cache entry")
        if path == marker:
            continue
        if not DEB_NAME.fullmatch(path.name):
            raise ValueError("unreviewed cache file")
        size = path.stat().st_size
        if not 8 < size <= MAX_PACKAGE_BYTES:
            raise ValueError("invalid package size")
        with path.open("rb") as stream:
            if stream.read(8) != b"!<arch>\n":
                raise ValueError("invalid Debian archive header")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("oversized cache")
        packages.append(path)

    if not MIN_PACKAGES <= len(packages) <= MAX_PACKAGES:
        raise ValueError("unexpected Debian package count")
    if output.is_symlink() or output.exists() or not output.parent.is_dir():
        raise ValueError("unsafe artifact destination")
    # Emit only allowlisted files; never tar -C cache . or git archive HEAD.
    with tarfile.open(output, mode="x:gz") as archive:
        for path in [marker, *sorted(packages)]:
            metadata = tarfile.TarInfo(f".gtk-deb-cache/{path.name}")
            metadata.size = path.stat().st_size
            metadata.mode = 0o644
            metadata.mtime = 0
            metadata.uid = metadata.gid = 0
            with path.open("rb") as stream:
                archive.addfile(metadata, stream)
    return len(packages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        count = build(args.cache, args.output)
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError, json.JSONDecodeError):
        # Do not echo untrusted paths, filenames, values or exceptions.
        print("GTK cache artifact refused")
        return 2
    print(f"GTK cache artifact accepted: {count} Debian packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
