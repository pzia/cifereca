#!/usr/bin/env python3
"""P0 #857: real full-release install plus Core rollback on injected failure.

The success path retains plugin-release.json and installs all shipped plugins.
The negative path fails after the Core switch, before plugin mutation; it tests
Core rollback, not the distinct plugins-only transactional rollback contract.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock


def check(ok: bool, description: str) -> None:
    if not ok:
        raise AssertionError(description)


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(item.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def inventory(cli: Path, directory: Path) -> list[dict]:
    result = subprocess.run(
        [str(cli), "plugin", "list"], cwd=directory, text=True,
        capture_output=True, timeout=150, check=False,
    )
    check(result.returncode == 0, f"plugin list failed: {result.stderr[-1000:]}")
    parsed = json.loads(result.stdout)
    check(isinstance(parsed, list), "plugin list is not a JSON array")
    return parsed


def run_gate(source: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="cifereca-full-release-p0-") as tmp:
        root = Path(tmp)
        paths = {
            "CIFERECA_INSTALL_ROOT": root / "runtime",
            "CIFERECA_BIN_DIR": root / "bin",
            "XDG_DATA_HOME": root / "data",
            "XDG_CONFIG_HOME": root / "config",
            "XDG_STATE_HOME": root / "state",
            "XDG_CACHE_HOME": root / "cache",
        }
        for key, location in paths.items():
            os.environ[key] = str(location)
            location.mkdir(parents=True, exist_ok=True)

        from cifereca import updater
        from cifereca.plugins import PackageStore

        expected = updater._release_plugin_manifest(source)
        check(bool(expected), "missing or empty original plugin-release.json")
        markers = {
            paths["XDG_DATA_HOME"] / "cifereca" / ".p0-user-data": b"keep data\n",
            paths["XDG_CONFIG_HOME"] / "cifereca" / ".p0-config": b"keep config\n",
            paths["XDG_STATE_HOME"] / "cifereca" / ".p0-state": b"keep state\n",
        }
        for path, value in markers.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)

        print(f"Full release install: {len(expected)} expected packages", flush=True)
        result = updater.install_source(source, keep=3)
        check(result.get("ok") is True, f"full install not confirmed: {result}")
        check(isinstance(result.get("plugins"), dict) and result["plugins"].get("ok") is True,
              "release plugins installation not confirmed")
        link = paths["CIFERECA_INSTALL_ROOT"] / "current"
        check(link.is_symlink(), "Core current symlink absent")
        installed_core = link.resolve(strict=True)
        check(installed_core == Path(result["current"]).resolve(),
              "Core current symlink points to wrong release")
        check(updater._release_plugin_manifest(installed_core) == expected,
              "full release manifest modified or omitted")

        store = PackageStore()
        packages = store.all_packages()
        check(bool(packages), "full release installed zero packages")
        # plugin-release.json is a frozen publication snapshot, not a pin
        # for everyday PR CI. Source installs package the current src/plugins/
        # manifests. Verify that actual source code is active; version drift
        # against the frozen publication list is only a release-time gate.
        source_versions = {}
        for path in sorted((source / "src/plugins").glob("*/cifereca-plugin.json")):
            package = json.loads(path.read_text(encoding="utf-8"))["package"]
            check(package["id"] not in source_versions,
                  f"duplicate package id in source: {package['id']}")
            source_versions[package["id"]] = str(package["version"])
        for package_id in expected:
            check(package_id in packages, f"missing release package {package_id}")
        for package_id in packages:
            version = store.active_version(package_id)
            check(version is not None, f"inactive package after full install: {package_id}")
            check(version == source_versions.get(package_id),
                  f"installed package/source version mismatch: {package_id} "
                  f"got {version}, source {source_versions.get(package_id)}")
        drift = sorted(pid for pid, version in expected.items()
                       if version != source_versions.get(pid))
        if drift:
            print(f"Publication snapshot differs from current source for "
                  f"{len(drift)} packages (informational in PR CI): {', '.join(drift)}",
                  flush=True)
        cli = installed_core / ".venv" / "bin" / "cifereca-cli"
        original_rows = inventory(cli, root)
        visible = {
            (str(row.get("package_id")), str(row.get("plugin_id"))): row
            for row in original_rows if isinstance(row, dict)
        }
        count = 0
        missing_contributions = []
        broken_contributions = []
        for package_id in packages:
            version = store.active_version(package_id)
            check(version is not None, f"inactive package after full install: {package_id}")
            for descriptor in store.load(package_id, version).manifest.plugins:
                label = f"{package_id}/{descriptor.id} ({descriptor.type})"
                row = visible.get((package_id, descriptor.id))
                if row is None:
                    missing_contributions.append(label)
                    continue
                if row.get("package_version") != version or row.get("state") in ("error", "incompatible"):
                    broken_contributions.append(f"{label}: {row.get('state')}/{row.get('package_version')}")
                count += 1
        if missing_contributions:
            print("Missing installed contributions: " + "; ".join(missing_contributions), flush=True)
        if broken_contributions:
            print("Broken installed contributions: " + "; ".join(broken_contributions), flush=True)
        check(not missing_contributions and not broken_contributions,
              f"full release inventory invalid: {len(missing_contributions)} missing, "
              f"{len(broken_contributions)} broken")
        print(f"Installed inventory OK: {len(packages)} packages / {count} contributions", flush=True)

        old_links = {
            name: updater._symlink_target(paths["CIFERECA_INSTALL_ROOT"] / name)
            for name in ("current", "previous")
        }
        old_releases = {p.resolve() for p in
                        (paths["CIFERECA_INSTALL_ROOT"] / "releases").iterdir() if p.is_dir()}
        old_plugins = digest_tree(store.root)
        old_markers = {p: p.read_bytes() for p in markers}

        # This deliberately fails *after* the real Core switch, without
        # touching the plugin store; plugins-only rollback is tested elsewhere.
        with mock.patch.object(
            updater, "_apply_release_plugin_snapshot",
            side_effect=updater.UpdateError("P0 forced post-switch failure"),
        ) as failed:
            try:
                updater.install_source(source, keep=3)
            except updater.UpdateError as exc:
                check("P0 forced" in str(exc), f"unexpected failure: {exc}")
            else:
                raise AssertionError("failed Core update unexpectedly returned success")
        check(failed.call_count == 1, "failure injection was not reached")
        for name, old in old_links.items():
            check(updater._symlink_target(paths["CIFERECA_INSTALL_ROOT"] / name) == old,
                  f"Core {name} not restored")
        releases_after = {p.resolve() for p in
                          (paths["CIFERECA_INSTALL_ROOT"] / "releases").iterdir() if p.is_dir()}
        check(releases_after == old_releases, "failed Core release directory remains")
        check(digest_tree(store.root) == old_plugins, "plugins changed after rollback")
        check(all(p.read_bytes() == b for p, b in old_markers.items()),
              "user XDG markers changed after rollback")
        check(inventory(cli, root) == original_rows, "installed inventory changed after rollback")
        print("Core rollback OK; plugin store and XDG data preserved", flush=True)


if __name__ == "__main__":
    try:
        run_gate(Path.cwd().resolve())
    except (AssertionError, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"P0 full-release gate FAILED: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
