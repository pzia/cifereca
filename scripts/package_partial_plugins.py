"""Build *preview* plugins-only snapshots using the existing update-dev format.

This is not a release publisher. In particular it never modifies VERSION, Core,
plugin-release.json or a GitHub tag.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
from typing import Sequence
import zipfile

from scripts.plugin_packaging import (
    DIST_DIR,
    PLUGINS_DIR,
    REPO_ROOT,
    PackagingError,
    build_dev_snapshot,
    discover_plugins,
    git_short_sha,
    read_plugin_manifest,
    sha256_file,
    validate_dev_snapshot,
)

PARTIAL_MANIFEST = "cifereca-plugins-only.json"
PARTIAL_KIND = "cifereca-plugins-only-preview"


def _package_records(archive: zipfile.ZipFile, snapshot_root: str, dev: dict) -> list[dict]:
    records = []
    for item in dev["plugins"]:
        path = item["path"]
        payload = archive.read(f"{snapshot_root}/{path}")
        with zipfile.ZipFile(BytesIO(payload)) as package_zip:
            manifest = json.loads(package_zip.read("cifereca-plugin.json"))
        package = manifest["package"]
        records.append({
            "package_id": package["id"],
            "version": str(package["version"]),
            "plugin_api": str(package.get("plugin_api") or ">=1,<2"),
            "plugins": sorted({
                str(plugin["id"]): str(plugin.get("version") or package["version"])
                for plugin in manifest["plugins"]
            }.items()),
            "path": path,
            "sha256": item["sha256"],
        })
    return sorted(records, key=lambda record: record["package_id"])


def verify_partial_snapshot(snapshot: Path) -> dict:
    """Verify that the embedded preview index describes exactly the ZIP payload."""
    dev = validate_dev_snapshot(snapshot)
    if dev.get("core"):
        raise PackagingError("snapshot plugins-only contient un Core")
    if not dev["plugins"]:
        raise PackagingError("snapshot plugins-only vide")
    with zipfile.ZipFile(snapshot) as archive:
        root = dev["id"]
        try:
            metadata = json.loads(archive.read(f"{root}/{PARTIAL_MANIFEST}"))
        except (KeyError, ValueError, UnicodeError) as exc:
            raise PackagingError("manifest plugins-only absent ou invalide") from exc
        actual = _package_records(archive, root, dev)
    if (
        not isinstance(metadata, dict)
        or metadata.get("format") != 1
        or metadata.get("kind") != PARTIAL_KIND
        or metadata.get("id") != dev["id"]
        or not metadata.get("core_version_tested")
        or metadata.get("packages") != actual
    ):
        raise PackagingError("manifest plugins-only incohérent avec les packages ZIP")
    return metadata


def build_partial_snapshot(
    package_ids: Sequence[str],
    *,
    plugins_dir: Path = PLUGINS_DIR,
    output_dir: Path = DIST_DIR,
    snapshot_id: str | None = None,
) -> Path:
    ids = [value.strip() for value in package_ids]
    if not ids or any(not value for value in ids):
        raise PackagingError("au moins un package id explicite est requis")
    if len(ids) != len(set(ids)):
        raise PackagingError("package id dupliqué dans la sélection")
    available = {}
    for directory in discover_plugins(plugins_dir):
        manifest = read_plugin_manifest(directory)
        package_id = manifest["package"]["id"]
        if package_id in available:
            raise PackagingError(f"package id dupliqué dans les sources: {package_id}")
        available[package_id] = directory
    missing = sorted(set(ids) - set(available))
    if missing:
        raise PackagingError(f"packages inconnus: {', '.join(missing)}")

    selected = [available[package_id] for package_id in sorted(ids)]
    name = snapshot_id or f"cifereca-plugins-only-preview-{git_short_sha()}"
    archive_path = build_dev_snapshot(selected, output_dir, snapshot_id=name)
    with zipfile.ZipFile(archive_path) as archive:
        dev = json.loads(archive.read(f"{name}/cifereca-dev.json"))
        records = _package_records(archive, name, dev)
    metadata = {
        "format": 1,
        "kind": PARTIAL_KIND,
        "id": name,
        "source_git_commit": git_short_sha(),
        "core_version_tested": (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "packages": records,
    }
    info = zipfile.ZipInfo(f"{name}/{PARTIAL_MANIFEST}", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr(info, json.dumps(metadata, sort_keys=True, indent=2, ensure_ascii=False) + "\n")

    verify_partial_snapshot(archive_path)
    archive_path.with_suffix(archive_path.suffix + ".sha256").write_text(
        f"{sha256_file(archive_path)}  {archive_path.name}\n", encoding="utf-8"
    )
    return archive_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a plugins-only snapshot (preview, no publication)")
    parser.add_argument("--packages", help="Comma-separated explicit package IDs")
    parser.add_argument("--plugins-dir", type=Path, default=PLUGINS_DIR)
    parser.add_argument("--out", type=Path, default=DIST_DIR)
    parser.add_argument("--id", dest="snapshot_id")
    parser.add_argument("--verify", type=Path, help="Verify an existing preview ZIP instead of building")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            result = verify_partial_snapshot(args.verify)
            print(f"OK: {result['id']} ({len(result['packages'])} packages)")
            return 0
        if not args.packages:
            parser.error("--packages est requis pour construire un snapshot")
        output = build_partial_snapshot(
            args.packages.split(","),
            plugins_dir=args.plugins_dir,
            output_dir=args.out,
            snapshot_id=args.snapshot_id,
        )
    except (PackagingError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(output)
    print(f"sha256: {sha256_file(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
