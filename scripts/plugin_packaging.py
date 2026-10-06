from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Iterable, Sequence
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"
DIST_DIR = REPO_ROOT / "dist"
PLUGIN_MANIFEST = "cifereca-plugin.json"
DEV_MANIFEST = "cifereca-dev.json"

CORE044_PLUGIN_MAX_FILES = 5000
CORE044_PLUGIN_MAX_UNCOMPRESSED = 256 * 1024 * 1024
CORE044_SNAPSHOT_MAX_FILES = 30000
CORE044_SNAPSHOT_MAX_UNCOMPRESSED = 2 * 1024 * 1024 * 1024

ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "dist",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


class PackagingError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackagingError(f"manifest absent: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PackagingError(f"JSON invalide: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackagingError(f"objet JSON attendu: {path}")
    return value


def read_plugin_manifest(plugin_dir: Path) -> dict:
    plugin_dir = plugin_dir.resolve()
    manifest = _read_json(plugin_dir / PLUGIN_MANIFEST)
    if manifest.get("manifest") != 1:
        raise PackagingError(f"{plugin_dir}: manifest=1 attendu")
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise PackagingError(f"{plugin_dir}: package absent")
    for key in ("id", "version"):
        if not isinstance(package.get(key), str) or not package[key].strip():
            raise PackagingError(f"{plugin_dir}: package.{key} absent")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package["id"]):
        raise PackagingError(f"{plugin_dir}: package.id invalide")
    descriptors = manifest.get("plugins")
    if not isinstance(descriptors, list) or not descriptors:
        raise PackagingError(f"{plugin_dir}: plugins[] vide ou absent")
    for index, descriptor in enumerate(descriptors):
        if not isinstance(descriptor, dict):
            raise PackagingError(f"{plugin_dir}: plugins[{index}] invalide")
        if not descriptor.get("id") or not descriptor.get("type"):
            raise PackagingError(
                f"{plugin_dir}: plugins[{index}] requiert id et type"
            )
    return manifest


def _package_slug(package_id: str) -> str:
    prefix = "org.cifereca."
    value = package_id[len(prefix):] if package_id.startswith(prefix) else package_id
    value = value.replace(".", "-")
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()
    if not value:
        raise PackagingError(f"package.id non transformable: {package_id}")
    return value


def package_zip_name(manifest: dict) -> str:
    package = manifest["package"]
    return (
        f"cifereca-plugin-{_package_slug(package['id'])}-"
        f"{package['version']}.zip"
    )


def _source_files(plugin_dir: Path) -> list[Path]:
    plugin_dir = plugin_dir.resolve()
    files: list[Path] = []
    total = 0
    for path in plugin_dir.rglob("*"):
        relative = path.relative_to(plugin_dir)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise PackagingError(f"symlink interdit dans un plugin: {path}")
        if not path.is_file() or path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
        total += path.stat().st_size
    files.sort(key=lambda p: p.relative_to(plugin_dir).as_posix())
    if len(files) > CORE044_PLUGIN_MAX_FILES:
        raise PackagingError(
            f"{plugin_dir}: plus de {CORE044_PLUGIN_MAX_FILES} fichiers"
        )
    if total > CORE044_PLUGIN_MAX_UNCOMPRESSED:
        raise PackagingError(
            f"{plugin_dir}: plus de {CORE044_PLUGIN_MAX_UNCOMPRESSED} octets"
        )
    if not any(path.name == PLUGIN_MANIFEST for path in files):
        raise PackagingError(f"{plugin_dir}: {PLUGIN_MANIFEST} absent")
    return files


def _zip_info(name: str, *, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_EPOCH)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = 0o755 if executable else 0o644
    info.external_attr = (mode & 0xFFFF) << 16
    return info


def _write_bytes(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    executable: bool = False,
) -> None:
    archive.writestr(
        _zip_info(name, executable=executable),
        payload,
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def discover_plugins(plugins_dir: Path = PLUGINS_DIR) -> list[Path]:
    plugins_dir = plugins_dir.resolve()
    if not plugins_dir.is_dir():
        raise PackagingError(f"répertoire plugins absent: {plugins_dir}")
    found = sorted(
        path.parent
        for path in plugins_dir.glob(f"*/{PLUGIN_MANIFEST}")
        if path.is_file()
    )
    if not found:
        raise PackagingError(f"aucun plugin sous {plugins_dir}")
    seen: dict[str, Path] = {}
    for plugin_dir in found:
        manifest = read_plugin_manifest(plugin_dir)
        package_id = manifest["package"]["id"]
        if package_id in seen:
            raise PackagingError(
                f"package.id dupliqué {package_id}: {seen[package_id]} / {plugin_dir}"
            )
        seen[package_id] = plugin_dir
    return found


def build_plugin_zip(plugin_dir: Path, output_dir: Path = DIST_DIR) -> Path:
    plugin_dir = plugin_dir.resolve()
    manifest = read_plugin_manifest(plugin_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / package_zip_name(manifest)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in _source_files(plugin_dir):
                relative = path.relative_to(plugin_dir).as_posix()
                _write_bytes(
                    archive,
                    relative,
                    path.read_bytes(),
                    executable=bool(path.stat().st_mode & 0o111),
                )
        validate_plugin_zip(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _safe_zip_member(info: zipfile.ZipInfo, *, label: str) -> PurePosixPath:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    windows = PureWindowsPath(name)
    mode = (info.external_attr >> 16) & 0xFFFF
    if (
        not name
        or path.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in path.parts
        or "\x00" in name
    ):
        raise PackagingError(f"{label}: chemin ZIP interdit: {info.filename!r}")
    if stat.S_ISLNK(mode):
        raise PackagingError(f"{label}: symlink ZIP interdit: {info.filename!r}")
    return path


def validate_plugin_zip(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > CORE044_PLUGIN_MAX_FILES:
                raise PackagingError(f"{path}: trop de fichiers")
            total = 0
            manifest_info = None
            for info in infos:
                _safe_zip_member(info, label=path.name)
                total += int(info.file_size)
                if total > CORE044_PLUGIN_MAX_UNCOMPRESSED:
                    raise PackagingError(f"{path}: archive décompressée trop grande")
                if info.filename.rstrip("/") == PLUGIN_MANIFEST:
                    manifest_info = info
            if manifest_info is None:
                raise PackagingError(
                    f"{path}: {PLUGIN_MANIFEST} doit être à la racine"
                )
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise PackagingError(f"ZIP plugin invalide: {path}") from exc
    if not isinstance(manifest, dict):
        raise PackagingError(f"{path}: manifest plugin invalide")
    package = manifest.get("package")
    if not isinstance(package, dict) or not package.get("id") or not package.get("version"):
        raise PackagingError(f"{path}: package.id/version absent")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(package["id"])):
        raise PackagingError(f"{path}: package.id invalide")
    return manifest


def _git_value(args: Sequence[str], default: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        value = proc.stdout.strip()
        return value or default
    except (OSError, subprocess.CalledProcessError):
        return default


def git_short_sha() -> str:
    return _git_value(["rev-parse", "--short=12", "HEAD"], "unknown")


def git_created_at() -> str:
    value = _git_value(["show", "-s", "--format=%cI", "HEAD"], "")
    if value:
        return value
    return datetime.now(timezone.utc).isoformat()


def _package_record(path: Path, manifest: dict) -> dict:
    package = manifest["package"]
    return {
        "package_id": package["id"],
        "version": package["version"],
        "filename": path.name,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def _build_all_plugin_zips(
    plugin_dirs: Sequence[Path], temporary_dir: Path
) -> list[tuple[Path, dict]]:
    records: list[tuple[Path, dict]] = []
    for plugin_dir in plugin_dirs:
        manifest = read_plugin_manifest(plugin_dir)
        package_zip = build_plugin_zip(plugin_dir, temporary_dir)
        records.append((package_zip, _package_record(package_zip, manifest)))
    records.sort(key=lambda item: item[1]["package_id"])
    return records


def _sha256sums(
    records: Iterable[tuple[Path, dict]], *, prefix: str = ""
) -> bytes:
    return (
        "".join(
            f"{record['sha256']}  {prefix}{path.name}\n"
            for path, record in records
        )
    ).encode("utf-8")


def build_all_in_one(
    plugin_dirs: Sequence[Path],
    output_dir: Path = DIST_DIR,
    *,
    filename: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = filename or f"cifereca-plugins-all-in-one-{git_short_sha()}.zip"
    destination = output_dir / filename
    with tempfile.TemporaryDirectory(prefix="cifereca-package-all-") as tmp:
        records = _build_all_plugin_zips(plugin_dirs, Path(tmp))
        bundle = {
            "format": 1,
            "kind": "cifereca-plugin-bundle",
            "created_at": git_created_at(),
            "source_git_commit": git_short_sha(),
            "plugins": [record for _, record in records],
        }
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            _write_bytes(archive, "cifereca-plugins.json", _json_bytes(bundle))
            _write_bytes(
                archive,
                "SHA256SUMS",
                _sha256sums(records, prefix="packages/"),
            )
            for package_zip, _ in records:
                _write_bytes(
                    archive,
                    f"packages/{package_zip.name}",
                    package_zip.read_bytes(),
                )
    return destination


def default_snapshot_id() -> str:
    return f"cifereca-dev-plugins-{git_short_sha()}"


def build_dev_snapshot(
    plugin_dirs: Sequence[Path],
    output_dir: Path = DIST_DIR,
    *,
    snapshot_id: str | None = None,
    filename: str | None = None,
    activate: bool = True,
) -> Path:
    snapshot_id = snapshot_id or default_snapshot_id()
    if (
        not snapshot_id
        or "/" in snapshot_id
        or "\\" in snapshot_id
        or snapshot_id in {".", ".."}
    ):
        raise PackagingError("id de snapshot invalide")
    filename = filename or f"{snapshot_id}.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / filename

    with tempfile.TemporaryDirectory(prefix="cifereca-dev-snapshot-") as tmp:
        records = _build_all_plugin_zips(plugin_dirs, Path(tmp))
        manifest = {
            "format": 1,
            "kind": "cifereca-dev-snapshot",
            "id": snapshot_id,
            "created_at": git_created_at(),
            "plugins": [
                {
                    "path": f"packages/{package_zip.name}",
                    "sha256": record["sha256"],
                    "activate": bool(activate),
                }
                for package_zip, record in records
            ],
        }
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            prefix = f"{snapshot_id}/"
            _write_bytes(
                archive,
                prefix + DEV_MANIFEST,
                _json_bytes(manifest),
            )
            _write_bytes(
                archive,
                prefix + "packages/SHA256SUMS",
                _sha256sums(records),
            )
            for package_zip, _ in records:
                _write_bytes(
                    archive,
                    prefix + f"packages/{package_zip.name}",
                    package_zip.read_bytes(),
                )
    validate_dev_snapshot(destination)
    return destination


def validate_dev_snapshot(path: Path) -> dict:
    """Validate the Core044 cifereca-update-dev snapshot contract."""

    try:
        archive = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise PackagingError("snapshot ZIP illisible") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > CORE044_SNAPSHOT_MAX_FILES:
            raise PackagingError("snapshot contient trop de fichiers")
        roots: set[str] = set()
        total = 0
        by_name = {}
        for info in infos:
            member = _safe_zip_member(info, label=path.name)
            roots.add(member.parts[0])
            total += int(info.file_size)
            if total > CORE044_SNAPSHOT_MAX_UNCOMPRESSED:
                raise PackagingError("snapshot décompressé trop grand")
            by_name[member.as_posix()] = info
        if len(roots) != 1:
            raise PackagingError("snapshot doit contenir une racine unique")
        root = next(iter(roots))
        manifest_name = f"{root}/{DEV_MANIFEST}"
        if manifest_name not in by_name:
            raise PackagingError(f"{DEV_MANIFEST} absent du snapshot")
        try:
            manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
        except Exception as exc:
            raise PackagingError("manifest dev invalide") from exc
        if not isinstance(manifest, dict) or type(manifest.get("format")) is not int:
            raise PackagingError("format de snapshot dev invalide")
        if manifest["format"] != 1:
            raise PackagingError("format de snapshot dev non supporté")
        if manifest.get("kind") != "cifereca-dev-snapshot":
            raise PackagingError("type de snapshot dev invalide")
        plugin_items = manifest.get("plugins", [])
        if not isinstance(plugin_items, list):
            raise PackagingError("liste plugins invalide")
        package_ids: set[str] = set()
        for item in plugin_items:
            if not isinstance(item, dict):
                raise PackagingError("entrée plugin snapshot invalide")
            relative = str(item.get("path") or "")
            posix = PurePosixPath(relative)
            if (
                not relative
                or posix.is_absolute()
                or ".." in posix.parts
                or PureWindowsPath(relative).is_absolute()
                or PureWindowsPath(relative).drive
            ):
                raise PackagingError(f"plugin hors snapshot: {relative}")
            member_name = f"{root}/{posix.as_posix()}"
            if member_name not in by_name:
                raise PackagingError(f"plugin absent du snapshot: {relative}")
            payload = archive.read(member_name)
            expected = str(item.get("sha256") or "")
            actual = hashlib.sha256(payload).hexdigest()
            if expected and actual != expected:
                raise PackagingError(f"SHA-256 plugin invalide: {relative}")
            try:
                with zipfile.ZipFile(
                    tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
                ):
                    pass
                with tempfile.NamedTemporaryFile(suffix=".zip") as handle:
                    handle.write(payload)
                    handle.flush()
                    plugin_manifest = validate_plugin_zip(Path(handle.name))
            except PackagingError:
                raise
            package_id = str(plugin_manifest["package"]["id"])
            if package_id in package_ids:
                raise PackagingError(
                    f"package plugin dupliqué dans snapshot: {package_id}"
                )
            package_ids.add(package_id)
        if not plugin_items and not manifest.get("core"):
            raise PackagingError("snapshot vide: core ou plugins requis")
        return manifest


def validate_with_update_dev(
    snapshot: Path,
    *,
    executable: str = "cifereca-update-dev",
) -> dict:
    resolved = shutil.which(executable)
    if resolved is None:
        raise PackagingError(f"exécutable introuvable: {executable}")
    proc = subprocess.run(
        [resolved, str(snapshot.resolve()), "--dry-run", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise PackagingError(
            f"cifereca-update-dev refuse le snapshot: {detail[-2000:]}"
        )
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PackagingError("sortie cifereca-update-dev non JSON") from exc
    if not isinstance(value, dict) or not value.get("ok"):
        raise PackagingError("cifereca-update-dev n'a pas validé le snapshot")
    return value


def verify_repository(plugins_dir: Path = PLUGINS_DIR) -> list[dict]:
    rows = []
    for plugin_dir in discover_plugins(plugins_dir):
        manifest = read_plugin_manifest(plugin_dir)
        files = _source_files(plugin_dir)
        rows.append(
            {
                "directory": plugin_dir.name,
                "package_id": manifest["package"]["id"],
                "version": manifest["package"]["version"],
                "descriptors": len(manifest["plugins"]),
                "files": len(files),
            }
        )
    return rows
