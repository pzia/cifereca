#!/usr/bin/env python3
"""Fetch and strictly unpack the published Debian 13 GTK4 offline cache.

Use ONLY on Debian trixie amd64 / Python 3.13. Does not install or change
system packages. The isolated runtime is created by bootstrap.py --offline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import urlopen

RELEASE_TAG = "gtk4-debian13-trixie-amd64-20261020"
ASSET = "gtk4-deb-cache-debian13-trixie-amd64.tar.gz"
RELEASE_BASE = f"https://github.com/pzia/cifereca/releases/download/{RELEASE_TAG}"
PROFILE = {"distro": "debian", "codename": "trixie", "architecture": "amd64"}
DEB_NAME = re.compile(r"[a-z0-9][a-z0-9+._~-]*_[a-zA-Z0-9.+:~_-]+_(?:all|amd64)\.deb")
CHECKSUM = re.compile(rf"([a-f0-9]{{64}})  {re.escape(ASSET)}\n?")
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024


def _verify_host() -> None:
    values = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    if (values.get("ID"), values.get("VERSION_CODENAME")) != ("debian", "trixie"):
        raise ValueError("unsupported host")
    if platform.machine() != "x86_64" or sys.version_info[:2] != (3, 13):
        raise ValueError("unsupported architecture or interpreter")


def _fetch(url: str, target: Path, limit: int) -> None:
    # URLs are fixed by this source file. Never build a URL from untrusted data.
    total = 0
    with urlopen(url, timeout=45) as response, target.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError("oversized release asset")
            output.write(chunk)
    if not total:
        raise ValueError("empty release asset")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unpack_checked(archive: Path, target: Path) -> int:
    count, size = 0, 0
    seen: set[str] = set()
    marker_found = False
    with tarfile.open(archive, "r:gz") as stream:
        for item in stream:
            if not item.isfile() or item.name in seen or item.size < 1:
                raise ValueError("unexpected tar member")
            seen.add(item.name)
            if not item.name.startswith(".gtk-deb-cache/"):
                raise ValueError("unexpected tar member")
            filename = item.name[len(".gtk-deb-cache/"):]
            if not filename or "/" in filename or "\\" in filename:
                raise ValueError("unexpected tar member")
            if filename == ".gtk4-cache-profile.json":
                if marker_found or item.size > 512:
                    raise ValueError("invalid platform marker")
                source = stream.extractfile(item)
                if source is None or json.load(source) != PROFILE:
                    raise ValueError("invalid platform marker")
                marker_found = True
                continue
            if (not DEB_NAME.fullmatch(filename)
                    or not 8 < item.size <= MAX_PACKAGE_BYTES):
                raise ValueError("unreviewed Debian archive")
            size += item.size
            count += 1
            if size > MAX_UNPACKED_BYTES or count > 300:
                raise ValueError("oversized Debian cache")
            source = stream.extractfile(item)
            if source is None:
                raise ValueError("missing Debian archive")
            with (target / filename).open("xb") as dest:
                if source.read(8) != b"!<arch>\n":
                    raise ValueError("invalid Debian package header")
                dest.write(b"!<arch>\n")
                shutil.copyfileobj(source, dest, length=1024 * 1024)
    if not marker_found or count < 11:
        raise ValueError("incomplete Debian cache")
    (target / ".gtk4-cache-profile.json").write_text(
        json.dumps(PROFILE, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", required=True, type=Path, help="new offline cache directory")
    parser.add_argument("--archive", type=Path, help="local asset, for disconnected testing")
    parser.add_argument("--sha256", help="expected 64-hex SHA256 when using --archive")
    args = parser.parse_args(argv)
    try:
        _verify_host()
        if (args.archive is None) != (args.sha256 is None):
            raise ValueError("local archive requires digest")
        destination = args.dest.expanduser().absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".gtk-cache-", dir=destination.parent) as tmp:
            scratch = Path(tmp)
            if args.archive is None:
                archive = scratch / ASSET
                checksums = scratch / "SHA256SUMS"
                _fetch(RELEASE_BASE + "/" + ASSET, archive, MAX_ARCHIVE_BYTES)
                _fetch(RELEASE_BASE + "/SHA256SUMS", checksums, 512)
                match = CHECKSUM.fullmatch(checksums.read_text(encoding="ascii"))
                if match is None:
                    raise ValueError("invalid release checksum")
                expected = match.group(1)
            else:
                archive = args.archive
                expected = args.sha256
                if (archive.is_symlink() or not archive.is_file()
                        or not 0 < archive.stat().st_size <= MAX_ARCHIVE_BYTES):
                    raise ValueError("invalid local archive")
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ValueError("invalid SHA256")
            if _sha256(archive) != expected:
                raise ValueError("checksum mismatch")
            stage = scratch / "cache"
            stage.mkdir()
            count = _unpack_checked(archive, stage)
            # Atomic publish; no modifications to an existing cache.
            os.rename(stage, destination)
        print(f"Verified Debian 13 GTK4 offline cache: {count} signed-repository packages")
        print("Next: python3 bootstrap.py --offline --deb-cache <cache-directory>")
        return 0
    except (OSError, ValueError, UnicodeError, tarfile.TarError, EOFError, OverflowError):
        print("GTK4 offline cache refused: missing release, invalid content or unsupported host", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
