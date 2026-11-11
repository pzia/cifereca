#!/usr/bin/env python3
"""Package the existing Ubuntu Work .deb cache and optionally publish a Release.

No APT networking: use the already downloaded cache and verify Work's offline
bootstrap before publishing. Publication requires --publish and GitHub CLI auth.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
REPOSITORY = "pzia/cifereca"
ASSET = "gtk4-deb-cache-ubuntu24-noble-amd64.tar.gz"
TAG_PATTERN = re.compile(r"gtk4-ubuntu24-noble-amd64-20[0-9]{6}")


def require_host() -> None:
    values = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    if ((values.get("ID"), values.get("VERSION_CODENAME")) != ("ubuntu", "noble")
            or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12)):
        raise ValueError("Ubuntu noble amd64 with Python 3.12 required")


def run(*args: str, cwd: Path | None = None, capture: bool = False):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          capture_output=capture)


def validator_module():
    path = ROOT / "scripts/ci-validate-gtk-relay-cache.py"
    spec = importlib.util.spec_from_file_location("gtk_validator", path)
    if spec is None or spec.loader is None:
        raise ValueError("missing validator")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(stage: Path, cache: Path) -> tuple[Path, str]:
    mod = validator_module()
    if not cache.is_dir() or cache.is_symlink():
        raise ValueError("missing Work package cache")
    packages = sorted(cache.glob("*.deb"))
    if not 11 <= len(packages) <= 300:
        raise ValueError("incomplete Work GTK package cache")
    reviewed = stage / "cache"
    reviewed.mkdir()
    for file in packages:
        if file.is_symlink() or not file.is_file():
            raise ValueError("unsafe Work package")
        name = unquote(file.name)
        if not mod.DEB_NAME.fullmatch(name):
            raise ValueError("invalid Ubuntu package filename")
        dst = reviewed / name
        if dst.exists():
            raise ValueError("duplicate Ubuntu package filename")
        shutil.copyfile(file, dst)
    (reviewed / mod.MARKER).write_text(
        json.dumps(mod.UBUNTU_PROFILE, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    archive = stage / ASSET
    mod.build(reviewed, archive, profile=mod.UBUNTU_PROFILE)
    with archive.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    (stage / "SHA256SUMS").write_text(f"{sha}  {ASSET}\n", encoding="ascii")
    return archive, sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="gtk4-ubuntu24-noble-amd64-20261111")
    parser.add_argument("--publish", action="store_true",
                        help="explicitly create an immutable prerelease on GitHub")
    args = parser.parse_args()
    try:
        require_host()
        if not TAG_PATTERN.fullmatch(args.tag):
            raise ValueError("invalid release tag")
        cache = BASE / "runtime/apt/archives"
        # Validate existing archive closure via actual offline Work bootstrap.
        run(str(BASE / "work"), "bootstrap", "--offline", cwd=BASE)
        run(str(BASE / "work"), "check-import", cwd=BASE)
        with tempfile.TemporaryDirectory(prefix="cifereca-noble-release-") as tmp:
            stage = Path(tmp)
            archive, sha = build(stage, cache)
            print(f"Reviewed Ubuntu GTK cache: SHA256 {sha}; file {archive.name}")
            if not args.publish:
                print("No Release uploaded. Repeat with --publish when approved.")
                return 0
            if shutil.which("gh") is None:
                raise ValueError("GitHub CLI required to publish")
            run("gh", "auth", "status", capture=True)
            existing = subprocess.run(
                ["gh", "release", "view", args.tag, "--repo", REPOSITORY],
                capture_output=True, text=True,
            )
            if existing.returncode == 0:
                raise ValueError("release tag already exists; refusing overwrite")
            # 'gh release create' refuses an existing release. No --clobber.
            run("gh", "release", "create", args.tag, str(archive),
                str(stage / "SHA256SUMS"), "--repo", REPOSITORY,
                "--target", "main", "--prerelease",
                "--title", "GTK4 Ubuntu 24.04 Noble rootless offline cache",
                "--notes", "Ubuntu Noble amd64 Python 3.12 offline Work GTK dependency cache. "
                           "Only validated packages and exact distro marker. Verify SHA256SUMS.")
            published = run(
                "gh", "release", "view", args.tag, "--repo", REPOSITORY,
                "--json", "assets", "--jq", ".assets[].name", capture=True,
            ).stdout.splitlines()
            if sorted(published) != sorted([ASSET, "SHA256SUMS"]):
                raise ValueError("published assets could not be verified")
            print(f"Published and verified two assets: {args.tag}, SHA256 {sha}")
            return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Ubuntu cache publication refused: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
