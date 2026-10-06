from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from packaging.version import Version

from cifereca.paths import data_dir

from .errors import PackageInstallError
from .manifest import MANIFEST_FILENAME, PluginPackageManifest, load_manifest

MAX_FILES = 5000
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    manifest: PluginPackageManifest
    root: Path
    sha256: str


class PackageStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (data_dir() / "plugins" / "packages")
        self.root.mkdir(parents=True, exist_ok=True)

    def package_root(self, package_id: str) -> Path:
        return self.root / package_id

    def version_root(self, package_id: str, version: str) -> Path:
        return self.package_root(package_id) / version

    def installed_versions(self, package_id: str) -> list[str]:
        root = self.package_root(package_id)
        if not root.exists():
            return []
        versions: list[tuple[Version, str]] = []
        for path in root.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                versions.append((Version(path.name), path.name))
            except Exception:
                continue
        return [raw for _parsed, raw in sorted(versions, reverse=True)]

    def all_packages(self) -> dict[str, list[str]]:
        if not self.root.exists():
            return {}
        return {
            path.name: self.installed_versions(path.name)
            for path in sorted(self.root.iterdir(), key=lambda value: value.name)
            if path.is_dir() and self.installed_versions(path.name)
        }

    def _pointer_path(self, package_id: str) -> Path:
        return self.package_root(package_id) / "current.json"

    def pointer(self, package_id: str) -> dict[str, str | None]:
        path = self._pointer_path(package_id)
        if not path.exists():
            return {"active": None, "previous": None}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {
            "active": str(value.get("active")) if value.get("active") else None,
            "previous": str(value.get("previous")) if value.get("previous") else None,
        }

    def active_version(self, package_id: str) -> str | None:
        pointer = self.pointer(package_id)
        active = pointer.get("active")
        if active and self.version_root(package_id, active).is_dir():
            return active
        return None

    def set_active(self, package_id: str, version: str) -> None:
        target = self.version_root(package_id, version)
        if not target.is_dir():
            raise PackageInstallError(f"package version is not installed: {package_id} {version}")
        old = self.active_version(package_id)
        payload = {"active": version, "previous": old if old != version else self.pointer(package_id).get("previous")}
        path = self._pointer_path(package_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def rollback(self, package_id: str) -> str:
        pointer = self.pointer(package_id)
        previous = pointer.get("previous")
        active = pointer.get("active") or self.active_version(package_id)
        if not previous or not self.version_root(package_id, previous).is_dir():
            candidates = [version for version in self.installed_versions(package_id) if version != active]
            previous = candidates[0] if candidates else None
        if not previous:
            raise PackageInstallError(f"no rollback version available for {package_id}")
        path = self._pointer_path(package_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"active": previous, "previous": active}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return previous

    def load(self, package_id: str, version: str | None = None) -> InstalledPackage:
        version = version or self.active_version(package_id)
        if not version:
            raise PackageInstallError(f"package is not installed: {package_id}")
        root = self.version_root(package_id, version)
        manifest = load_manifest(root / MANIFEST_FILENAME)
        metadata_path = root / ".cifereca-install.json"
        sha256 = ""
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(metadata, dict):
                    sha256 = str(metadata.get("sha256") or "")
            except Exception:
                pass
        return InstalledPackage(manifest=manifest, root=root, sha256=sha256)

    def active_packages(self) -> list[InstalledPackage]:
        out: list[InstalledPackage] = []
        for package_id in sorted(self.all_packages()):
            try:
                out.append(self.load(package_id))
            except Exception:
                # Manager diagnostics handles broken packages without preventing startup.
                continue
        return out


class PackageInstaller:
    def __init__(self, store: PackageStore) -> None:
        self.store = store

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _validate_member(info: zipfile.ZipInfo) -> None:
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        windows_path = PureWindowsPath(name)
        if (
            not name
            or name.startswith("/")
            or path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in path.parts
        ):
            raise PackageInstallError(f"unsafe plugin archive path: {info.filename!r}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise PackageInstallError(f"plugin archive symlink is not allowed: {info.filename!r}")
        if "\x00" in name:
            raise PackageInstallError("plugin archive contains a NUL path")

    def inspect(self, archive: Path) -> PluginPackageManifest:
        archive = Path(archive)
        if not archive.is_file():
            raise PackageInstallError(f"plugin package not found: {archive}")
        try:
            with zipfile.ZipFile(archive, "r") as bundle:
                infos = bundle.infolist()
                if len(infos) > MAX_FILES:
                    raise PackageInstallError(f"plugin archive contains too many files ({len(infos)})")
                total = 0
                manifest_info: zipfile.ZipInfo | None = None
                for info in infos:
                    self._validate_member(info)
                    total += int(info.file_size)
                    if total > MAX_UNCOMPRESSED_BYTES:
                        raise PackageInstallError("plugin archive is too large when extracted")
                    if info.filename.rstrip("/") == MANIFEST_FILENAME:
                        manifest_info = info
                if manifest_info is None:
                    raise PackageInstallError(f"plugin archive must contain {MANIFEST_FILENAME} at its root")
                try:
                    value = json.loads(bundle.read(manifest_info).decode("utf-8"))
                except Exception as exc:
                    raise PackageInstallError(f"invalid plugin manifest: {exc}") from exc
        except zipfile.BadZipFile as exc:
            raise PackageInstallError(f"invalid plugin ZIP: {exc}") from exc
        if not isinstance(value, dict):
            raise PackageInstallError("plugin manifest root must be an object")
        from .manifest import parse_manifest
        return parse_manifest(value)

    def install(self, archive: Path, *, activate: bool = True, replace: bool = False) -> InstalledPackage:
        archive = Path(archive)
        manifest = self.inspect(archive)
        package_id = manifest.package.id
        version = manifest.package.version
        final = self.store.version_root(package_id, version)
        sha256 = self._sha256(archive)
        if final.exists() and not replace:
            installed = self.store.load(package_id, version)
            if installed.sha256 and installed.sha256 != sha256:
                raise PackageInstallError(
                    f"package {package_id} {version} is already installed with a different SHA-256"
                )
            if activate:
                self.store.set_active(package_id, version)
            return installed

        final.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=final.parent))
        try:
            with zipfile.ZipFile(archive, "r") as bundle:
                for info in bundle.infolist():
                    self._validate_member(info)
                    name = info.filename.replace("\\", "/")
                    destination = staging.joinpath(*PurePosixPath(name).parts)
                    if info.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(info, "r") as src, destination.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
            # Reparse the extracted manifest before making the version visible.
            extracted = load_manifest(staging / MANIFEST_FILENAME)
            if extracted.package.id != package_id or extracted.package.version != version:
                raise PackageInstallError("plugin manifest changed while extracting")
            (staging / ".cifereca-install.json").write_text(
                json.dumps({"sha256": sha256, "source": archive.name, "installed_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n",
                encoding="utf-8",
            )
            if final.exists():
                backup = final.with_name(f".{final.name}.replace-{os.getpid()}")
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                os.replace(final, backup)
                try:
                    os.replace(staging, final)
                except Exception:
                    os.replace(backup, final)
                    raise
                else:
                    shutil.rmtree(backup, ignore_errors=True)
            else:
                os.replace(staging, final)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        if activate:
            self.store.set_active(package_id, version)
        return self.store.load(package_id, version)

    def uninstall_version(self, package_id: str, version: str) -> None:
        target = self.store.version_root(package_id, version)
        if not target.exists():
            return
        active = self.store.active_version(package_id)
        shutil.rmtree(target)
        if active == version:
            remaining = self.store.installed_versions(package_id)
            pointer = self.store._pointer_path(package_id)
            if remaining:
                self.store.set_active(package_id, remaining[0])
            else:
                pointer.unlink(missing_ok=True)
