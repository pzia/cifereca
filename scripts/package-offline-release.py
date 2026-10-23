#!/usr/bin/env python3
"""Build an installable, single-root Core ZIP from a trusted checkout.

The release producer MUST sign the resulting ZIP. A wheelhouse-provenance.json
inside a modified ZIP is not, by itself, authentication or an authorization to
install arbitrary wheels (#2770).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cifereca.plugins.python_quarantine import QuarantineRejected
from cifereca.wheelhouse_quarantine import verify_wheelhouse

MAX_FILES = 20000
MAX_BYTES = 1024 * 1024 * 1024
SAFE_ROOT = "cifereca-offline"


def _tracked(source: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z", "--cached"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45, check=False,
    )
    if result.returncode:
        raise ValueError("release_tracked_files_unavailable")
    rows: list[Path] = []
    for name in result.stdout.split(b"\0"):
        if not name:
            continue
        # Use OS bytes decode to preserve only safe, actual tracked paths.
        raw = os.fsdecode(name)
        relative = PurePosixPath(raw)
        if (relative.is_absolute() or ".." in relative.parts
                or not relative.parts or any(part == ".git" for part in relative.parts)):
            raise ValueError("release_unsafe_tracked_path")
        path = source.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError("release_unusable_tracked_file")
        rows.append(path)
    if len(rows) > MAX_FILES:
        raise ValueError("release_too_many_files")
    return tuple(rows)


def create_archive(source: Path, output: Path, *, files: tuple[Path, ...] | None = None) -> Path:
    source = source.resolve()
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    version = (source / "VERSION").read_text(encoding="utf-8").strip()
    if not version or project["project"]["version"] != version:
        raise ValueError("release_version_invalid")
    wheelhouse = source / "offline-wheels"
    verified = verify_wheelhouse(wheelhouse, core_version=version)
    if not any(wheel.name.startswith("cifereca-" + version + "-") for wheel in verified):
        raise ValueError("release_core_wheel_missing")
    paths = list(files if files is not None else _tracked(source))
    paths.extend(verified)
    paths.append(wheelhouse / "wheelhouse-provenance.json")
    seen: set[str] = set()
    size = 0
    members: list[tuple[str, Path]] = []
    for item in paths:
        path = Path(item)
        if path.is_symlink() or not path.is_file() or path.resolve() != path:
            raise ValueError("release_file_unavailable")
        try:
            relative = path.relative_to(source).as_posix()
        except ValueError:
            raise ValueError("release_untrusted_path") from None
        if relative in seen:
            raise ValueError("release_duplicate_path")
        seen.add(relative)
        if ".." in PurePosixPath(relative).parts or relative.startswith("/"):
            raise ValueError("release_untrusted_path")
        size += path.stat().st_size
        if size > MAX_BYTES or len(seen) > MAX_FILES:
            raise ValueError("release_size_limit")
        members.append((relative, path))
    if not {"pyproject.toml", "VERSION"} <= seen:
        raise ValueError("release_missing_metadata")
    output = output.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError("release_output_exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for relative, path in sorted(members):
                info = zipfile.ZipInfo(f"{SAFE_ROOT}/{relative}", (2024, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as src, archive.open(info, "w") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
        with zipfile.ZipFile(output) as test:
            if test.testzip() is not None:
                raise ValueError("release_archive_corrupt")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = create_archive(ROOT, args.out)
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired, QuarantineRejected) as exc:
        # Never propagate upstream URLs or private file paths into the log.
        print("release_offline_admission_failed", file=sys.stderr)
        return 2
    print(output.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
