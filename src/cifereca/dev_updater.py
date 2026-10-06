from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from . import updater
from .paths import data_dir, state_dir
from .install_paths import ArchiveNotFoundError, resolve_archive

DEV_MANIFEST = "cifereca-dev.json"
PLUGIN_MANIFEST = "cifereca-plugin.json"
DEV_FORMAT = 1
MAX_FILES = 30000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


class DevUpdateError(RuntimeError):
    pass


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename.replace("\\", "/")
    p = PurePosixPath(name)
    wp = PureWindowsPath(name)
    mode = (info.external_attr >> 16) & 0xFFFF
    if not name or p.is_absolute() or wp.is_absolute() or bool(wp.drive) or ".." in p.parts:
        raise DevUpdateError(f"chemin interdit dans le snapshot dev: {info.filename}")
    if stat.S_ISLNK(mode):
        raise DevUpdateError(f"symlink ZIP interdit dans le snapshot dev: {info.filename}")
    return p


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_plugin_package_id(archive: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(archive, "r") as z:
            raw = json.loads(z.read(PLUGIN_MANIFEST).decode("utf-8"))
    except Exception as exc:
        raise DevUpdateError(f"plugin ZIP invalide: {archive.name}: {exc}") from exc
    package = raw.get("package") if isinstance(raw, dict) else None
    if not isinstance(package, dict) or not package.get("id") or not package.get("version"):
        raise DevUpdateError(f"manifest plugin incomplet: {archive.name}")
    package_id = str(package['id'])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', package_id):
        raise DevUpdateError('identifiant package plugin invalide')
    # Validate the complete manifest before any mutation, including entrypoints/API.
    try:
        from .plugins.manifest import parse_manifest
        parse_manifest(raw)
    except Exception as exc:
        raise DevUpdateError(f'manifest plugin invalide: {archive.name}') from exc
    return package_id, str(package['version'])


def _extract_snapshot(archive: Path, destination: Path) -> tuple[Path, dict[str, Any]]:
    try:
        z = zipfile.ZipFile(archive, "r")
    except Exception as exc:
        raise DevUpdateError("snapshot dev ZIP illisible") from exc
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise DevUpdateError("snapshot dev contient trop de fichiers")
        total = 0
        roots: set[str] = set()
        for info in infos:
            p = _safe_member(info)
            total += int(info.file_size)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise DevUpdateError("snapshot dev trop volumineux après extraction")
            roots.add(p.parts[0])
        if len(roots) != 1:
            raise DevUpdateError("snapshot dev doit contenir une racine unique")
        for info in infos:
            p = _safe_member(info)
            target = destination.joinpath(*p.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    root = destination / next(iter(roots))
    manifest_path = root / DEV_MANIFEST
    if not manifest_path.is_file():
        raise DevUpdateError(f"{DEV_MANIFEST} absent du snapshot")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DevUpdateError("manifest dev invalide") from exc
    if not isinstance(manifest, dict) or type(manifest.get("format")) is not int or manifest["format"] != DEV_FORMAT:
        raise DevUpdateError("format de snapshot dev non supporté")
    if str(manifest.get("kind") or "") != "cifereca-dev-snapshot":
        raise DevUpdateError("type de snapshot dev invalide")
    return root, manifest


def _plugin_store_root() -> Path:
    return data_dir() / "plugins" / "packages"


def _backup_plugins(package_ids: list[str], backup_root: Path) -> None:
    store = _plugin_store_root()
    backup_root.mkdir(parents=True, exist_ok=True)
    for package_id in package_ids:
        source = store / package_id
        if source.exists():
            shutil.copytree(source, backup_root / package_id, symlinks=False)


def _restore_plugins(package_ids: list[str], backup_root: Path) -> None:
    store = _plugin_store_root()
    store.mkdir(parents=True, exist_ok=True)
    for package_id in package_ids:
        target = store / package_id
        if target.exists():
            shutil.rmtree(target)
        saved = backup_root / package_id
        if saved.exists():
            shutil.copytree(saved, target, symlinks=False)


def _new_cli() -> Path:
    current = updater.install_root() / "current"
    return updater._command_path(current.resolve(), "cifereca-cli")


def _package_is_active(package_id: str) -> bool | None:
    root = _plugin_store_root() / package_id
    if not root.exists():
        return None
    pointer = root / "current.json"
    if not pointer.exists():
        return False
    try:
        value = json.loads(pointer.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(isinstance(value, dict) and value.get("active"))


def _install_plugin_replace(archive: Path, *, activate: bool) -> None:
    cli = _new_cli()
    command = [str(cli), "plugin", "install", str(archive), "--replace"]
    if not activate:
        command.append("--no-activate")
    proc = subprocess.run(
        command,
        text=True, capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise DevUpdateError(f"installation plugin dev échouée ({archive.name}): {detail[-1200:]}")


def _smoke_plugins(package_ids: list[str] | None = None) -> dict[str, Any]:
    cli = _new_cli()
    proc = subprocess.run(
        [str(cli), "plugin", "list"], text=True, capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise DevUpdateError(f"smoke plugins échoué: {detail[-1200:]}")
    try:
        rows = json.loads(proc.stdout)
    except Exception as exc:
        raise DevUpdateError("smoke plugins: JSON invalide") from exc
    if not isinstance(rows, list):
        raise DevUpdateError("smoke plugins: liste JSON attendue")
    failed = [row.get('plugin_id') for row in rows if isinstance(row, dict)
        and (package_ids is None or row.get('package_id') in package_ids)
        and row.get('state') in {'error', 'incompatible'}]
    if failed:
        raise DevUpdateError('smoke plugins: erreur des contributions installées: ' + ', '.join(map(str, failed)))
    return {"plugin_count": len(rows)}


def apply_snapshot(
    archive: Path,
    *,
    python: str | None = None,
    keep: int = 3,
    dry_run: bool = False,
    restart: bool = False,
) -> dict[str, Any]:
    try:
        archive = resolve_archive(archive)
    except ArchiveNotFoundError as exc:
        raise DevUpdateError(str(exc)) from exc
    with tempfile.TemporaryDirectory(prefix="cifereca-dev-update-") as tmp:
        root, manifest = _extract_snapshot(archive, Path(tmp))
        snapshot_id = str(manifest.get("id") or archive.stem)
        core_rel = str(manifest.get("core") or "")
        core_zip = (root / core_rel).resolve() if core_rel else None
        if core_zip is not None and (not core_zip.is_file() or root.resolve() not in core_zip.parents):
            raise DevUpdateError("archive core absente ou hors snapshot")
        plugin_items = manifest.get("plugins", [])
        if not isinstance(plugin_items, list):
            raise DevUpdateError("liste plugins invalide")
        plugin_archives: list[tuple[Path, bool]] = []
        package_ids: list[str] = []
        for item in plugin_items:
            rel = str(item.get("path") if isinstance(item, dict) else item)
            plugin_zip = (root / rel).resolve()
            if not plugin_zip.is_file() or root.resolve() not in plugin_zip.parents:
                raise DevUpdateError(f"plugin absent ou hors snapshot: {rel}")
            package_id, _version = _read_plugin_package_id(plugin_zip)
            if package_id in package_ids:
                raise DevUpdateError(f"package plugin dupliqué dans snapshot: {package_id}")
            expected_sha = str(item.get("sha256") or "") if isinstance(item, dict) else ""
            if expected_sha and _sha256(plugin_zip) != expected_sha:
                raise DevUpdateError(f"SHA-256 plugin invalide: {rel}")
            installed_active = _package_is_active(package_id)
            requested_activate = bool(item.get("activate", True)) if isinstance(item, dict) else True
            activate = requested_activate if installed_active is None else installed_active
            package_ids.append(package_id)
            plugin_archives.append((plugin_zip, activate))
        if core_zip is None and not plugin_archives:
            raise DevUpdateError('snapshot vide: core ou plugins requis')
        core_sha = str(manifest.get("core_sha256") or "")
        if core_sha and (core_zip is None or _sha256(core_zip) != core_sha):
            raise DevUpdateError("SHA-256 du core invalide")
        result: dict[str, Any] = {
            "ok": True,
            "snapshot_id": snapshot_id,
            "archive": str(archive),
            "sha256": _sha256(archive),
            "plugins": package_ids,
            "dry_run": bool(dry_run),
        }
        if dry_run:
            result['core'] = updater.install_archive(core_zip, python=python, keep=keep, dry_run=True) if core_zip else None
            result['core_changed'] = core_zip is not None
            return result

        # Manifest ids are labels, never filesystem paths. Keep previous backups.
        backup_name = hashlib.sha256(snapshot_id.encode()).hexdigest()[:16] + '-' + str(time.time_ns())
        backup_base = state_dir() / "dev-updates" / backup_name
        backup_plugins = backup_base / "plugins"
        _backup_plugins(package_ids, backup_plugins)
        old_current = updater._symlink_target(updater.install_root() / "current")
        core_installed = False
        core_result = None
        try:
            if core_zip is not None:
                core_result = updater.install_archive(core_zip, python=python, keep=max(keep, 3), dry_run=False)
                core_installed = True
            for plugin_zip, activate in plugin_archives:
                _install_plugin_replace(plugin_zip, activate=activate)
            smoke = _smoke_plugins(package_ids)
        except Exception as exc:
            restoration_errors = []
            try:
                _restore_plugins(package_ids, backup_plugins)
            except Exception:
                restoration_errors.append('plugins')
            current = updater._symlink_target(updater.install_root() / 'current')
            if core_zip is not None and old_current is not None and old_current.is_dir() and (core_installed or current != old_current):
                try:
                    updater.rollback(old_current.name)
                except Exception:
                    restoration_errors.append('core')
            if restoration_errors:
                raise DevUpdateError('Mise à jour échouée ; restauration incomplète : ' + ', '.join(restoration_errors)) from exc
            raise
        result.update({"core": core_result, "core_changed": core_installed, "smoke": smoke, "backup": str(backup_base)})
        if restart:
            app = updater._command_path(updater.install_root() / "current", "cifereca")
            try:
                subprocess.Popen([str(app)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                result["restarted"] = True
            except Exception as exc:
                result["restarted"] = False
                result["restart_error"] = str(exc)
        return result


def _print(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        for key, val in value.items():
            print(f"{key}: {val}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Updater de snapshots de développement cifereca")
    parser.add_argument("snapshot", nargs="?", help="ZIP snapshot (core et/ou plugins), dossier courant puis Téléchargements XDG")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--restart", action="store_true", help="Lance la nouvelle application après mise à jour")
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--python")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.snapshot:
        parser.error("snapshot ZIP requis")
    try:
        result = apply_snapshot(
            args.snapshot, python=args.python, keep=args.keep,
            dry_run=args.dry_run, restart=args.restart,
        )
        _print(result, args.json)
        return 0
    except (DevUpdateError, updater.UpdateError) as exc:
        _print({"ok": False, "error": {"code": "dev_update_error", "message": str(exc)}}, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
